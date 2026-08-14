"""
Advanced Engineering RAG - Parent/Child hybrid retrieval architecture
======================================================================

Implements a SOTA multi-vector retrieval architecture:
1. Parent Documents (InMemoryByteStore) - coarse-grained documents split by header hierarchy
2. Child Documents (Chroma + BM25) - fine-grained semantic chunks
3. Hybrid Search - Dense (MultiVectorRetriever) + Sparse (BM25)
4. Cross-Encoder Reranking - local Jina Reranker reranking

Dependencies:
- OpenAI Embeddings (text-embedding-3-small)
- CrossEncoder (jina-reranker-v3 local model)
- Chroma (vector store)
- BM25Retriever (sparse retrieval)
- MarkdownHeaderTextSplitter / RecursiveCharacterTextSplitter
"""

import os
import uuid
import hashlib
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple, Iterator

# LangChain Core
from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStoreRetriever

# LangChain Text Splitters
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

# LangChain Community - Vector Stores & Retrievers
from langchain_community.vectorstores import Chroma
from langchain_community.retrievers import BM25Retriever
from langchain_core.retrievers import BaseRetriever

# Official OpenAI API for embeddings
from openai import OpenAI

# Transformers for Reranking (with trust_remote_code for Jina custom arch)
import torch
from transformers import AutoModel, AutoTokenizer

# Storage (Abstract Base Class for ByteStore)
from abc import ABC, abstractmethod

# Local modules
from cadagent.services.table_processor import HTMLTableUnroller
from cadagent.config import create_openai_client


class PersistentByteStore(ABC):
    """
    Abstract base class for a persistent byte store

    Provides a basic key-value storage interface backed by JSON file persistence.
    Used to store Parent documents so they can be restored after a restart.
    """

    def __init__(self, persist_path: str):
        """
        Initialize the persistent store

        Args:
            persist_path: persistence file path (.json)
        """
        self.persist_path = persist_path
        self._store: Dict[str, bytes] = {}
        self._load()

    def _load(self):
        """Load data from disk"""
        if os.path.exists(self.persist_path):
            try:
                import json
                with open(self.persist_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Deserialize: JSON cannot store bytes directly, needs decoding
                    self._store = {
                        k: v.encode('utf-8') if isinstance(v, str) else v
                        for k, v in data.items()
                    }
                print(f"[PersistentByteStore] Loaded {len(self._store)} items from disk")
            except Exception as e:
                print(f"[PersistentByteStore] Failed to load: {e}")
                self._store = {}

    def _save(self):
        """Save data to disk"""
        try:
            import json
            os.makedirs(os.path.dirname(self.persist_path) or '.', exist_ok=True)
            # Serialize: convert bytes into JSON-serializable strings
            data = {
                k: v.decode('utf-8') if isinstance(v, bytes) else v
                for k, v in self._store.items()
            }
            with open(self.persist_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[PersistentByteStore] Failed to save: {e}")

    @abstractmethod
    def mget(self, keys: List[str]) -> List[Optional[bytes]]:
        """Get multiple values"""
        pass

    @abstractmethod
    def mset(self, key_value_pairs: List[Tuple[str, bytes]]) -> None:
        """Set multiple key-value pairs"""
        pass

    @abstractmethod
    def mdelete(self, keys: List[str]) -> None:
        """Delete multiple keys"""
        pass

    def yield_keys(self, start_key: Optional[str] = None) -> Iterator[str]:
        """Iterate keys"""
        keys = sorted(self._store.keys())
        if start_key:
            keys = [k for k in keys if k >= start_key]
        for key in keys:
            yield key

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, key: str) -> bool:
        return key in self._store


class DocumentPersistentStore(PersistentByteStore):
    """
    Document-specific persistent store

    Stores LangChain Document objects using JSON serialization
    """

    def __init__(self, persist_path: str):
        super().__init__(persist_path)

    def _load(self):
        """Load documents"""
        if os.path.exists(self.persist_path):
            try:
                import json
                with open(self.persist_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._store = {}
                    for key, doc_data in data.items():
                        # Deserialize Document
                        self._store[key] = self._deserialize_document(doc_data)
                print(f"[DocumentPersistentStore] Loaded {len(self._store)} documents from disk")
            except Exception as e:
                print(f"[DocumentPersistentStore] Failed to load: {e}")
                self._store = {}

    def _save(self):
        """Save documents"""
        try:
            import json
            os.makedirs(os.path.dirname(self.persist_path) or '.', exist_ok=True)
            data = {
                key: self._serialize_document(doc)
                for key, doc in self._store.items()
            }
            with open(self.persist_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"[DocumentPersistentStore] Saved {len(self._store)} documents to disk")
        except Exception as e:
            print(f"[DocumentPersistentStore] Failed to save: {e}")

    def _serialize_document(self, doc: Document) -> dict:
        """Serialize a Document object"""
        return {
            'page_content': doc.page_content,
            'metadata': doc.metadata,
        }

    def _deserialize_document(self, data: dict) -> Document:
        """Deserialize a Document object"""
        return Document(
            page_content=data.get('page_content', ''),
            metadata=data.get('metadata', {}),
        )

    def mget(self, keys: List[str]) -> List[Optional[Document]]:
        """Get multiple documents"""
        return [self._store.get(key) for key in keys]

    def mset(self, key_value_pairs: List[Tuple[str, Document]]) -> None:
        """Set multiple documents"""
        for key, doc in key_value_pairs:
            self._store[key] = doc
        self._save()

    def mdelete(self, keys: List[str]) -> None:
        """Delete multiple documents"""
        for key in keys:
            if key in self._store:
                del self._store[key]
        self._save()


class OpenAIEmbeddingsWrapper:
    """
    Embedding model wrapper using the official OpenAI API

    Implements an interface compatible with LangChain OpenAIEmbeddings,
    for use with the Chroma vector store.
    """

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        dimensions: Optional[int] = None,
    ):
        """
        Initialize the embedding model wrapper

        Args:
            model: OpenAI embedding model name
            dimensions: embedding vector dimensions (only text-embedding-3 supports this)
        """
        self.model = model
        self.dimensions = dimensions

        # Use the enterprise internal API client
        self.client = create_openai_client()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        Embed documents in batch

        Args:
            texts: document list

        Returns:
            list of embedding vectors
        """
        # Filter empty texts
        texts = [text or "" for text in texts]

        response = self.client.embeddings.create(
            model=self.model,
            input=texts,
            dimensions=self.dimensions,
        )

        return [item.embedding for item in response.data]

    def embed_query(self, text: str) -> List[float]:
        """
        Embed a single query

        Args:
            text: query text

        Returns:
            embedding vector
        """
        response = self.client.embeddings.create(
            model=self.model,
            input=text,
            dimensions=self.dimensions,
        )

        return response.data[0].embedding

    def __call__(self, text: str) -> List[float]:
        """
        Make the class callable, compatible with the LangChain interface
        """
        return self.embed_query(text)


class AdvancedEngineeringRAG:
    """
    Advanced engineering RAG system

    Architecture:
    1. HTML table unrolling -> solves the lost-header problem
    2. Parent splitting (MarkdownHeaderTextSplitter) -> InMemoryByteStore
    3. Child splitting (RecursiveCharacterTextSplitter) -> Chroma + BM25
    4. Hybrid retrieval (Dense + Sparse) -> CrossEncoder reranking
    """

    def __init__(
        self,
        docs_path: Optional[str] = None,
        reranker_model_path: str = "models",
        reranker_max_length: int = 512,
        chroma_persist_dir: str = "knowledge/vectorstore",
        collection_name: str = "engineering_docs",
        child_chunk_size: int = 250,
        child_chunk_overlap: int = 30,
    ):
        """
        Initialize the advanced engineering RAG system

        Args:
            docs_path: document path (cleaned Markdown files)
            reranker_model_path: local Reranker model path
            reranker_max_length: Reranker max sequence length
            chroma_persist_dir: Chroma persistence directory
            collection_name: Chroma collection name
            child_chunk_size: child document chunk size
            child_chunk_overlap: child document chunk overlap
        """
        self.docs_path = docs_path
        self.reranker_model_path = reranker_model_path
        self.reranker_max_length = reranker_max_length
        self.chroma_persist_dir = chroma_persist_dir
        self.collection_name = collection_name
        self.child_chunk_size = child_chunk_size
        self.child_chunk_overlap = child_chunk_overlap

        # Table unroller
        self.table_unroller = HTMLTableUnroller()

        # Parent store (persistent DocumentPersistentStore)
        parent_store_path = os.path.join(chroma_persist_dir, "parent_docs.json")
        self.parent_store: DocumentPersistentStore = DocumentPersistentStore(parent_store_path)

        # Chroma vector store (Child storage)
        self.vectorstore: Optional[Chroma] = None

        # BM25 sparse retriever
        self.bm25_retriever: Optional[BM25Retriever] = None

        # Initialize models
        self._init_models()

        # Index status
        self._indexed = False

    def _init_models(self):
        """Initialize the embedding model and the reranking model"""

        # 1. OpenAI Embeddings (text-embedding-3-small) - uses the enterprise internal API
        self.embeddings = OpenAIEmbeddingsWrapper(
            model="text-embedding-3-small",
        )

        # 2. Load the Transformers model directly (local Jina Reranker)
        # Note: trust_remote_code=True is required to load the custom JinaForRanking architecture
        model_path = self.reranker_model_path

        # Check whether the model exists
        if not os.path.exists(model_path):
            # Fall back to the HuggingFace model name
            model_path = "jinaai/jina-reranker-v3"

        try:
            # Load directly with AutoModel, supporting trust_remote_code
            # AutoModel loads JinaForRanking based on the auto_map in config.json
            self.reranker_model = AutoModel.from_pretrained(
                model_path,
                trust_remote_code=True,
                torch_dtype=torch.bfloat16,
            )
            self.reranker_model.eval()

            # Load the tokenizer
            self.reranker_tokenizer = AutoTokenizer.from_pretrained(
                model_path,
                trust_remote_code=True,
            )

            # Get the special tokens
            self.doc_embed_token_id = self.reranker_tokenizer.convert_tokens_to_ids("<|embed_token|>")
            self.query_embed_token_id = self.reranker_tokenizer.convert_tokens_to_ids("<|rerank_token|>")

            print(f"[AdvancedRAG] Reranker loaded: {model_path}")
            print(f"[AdvancedRAG] Reranker model type: {type(self.reranker_model).__name__}")
        except Exception as e:
            print(f"[AdvancedRAG] Warning: Failed to load reranker: {e}")
            self.reranker_model = None
            self.reranker_tokenizer = None

    def _generate_doc_id(self, content: str) -> str:
        """Generate a unique document ID"""
        # Content hash + UUID to ensure uniqueness
        content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
        unique_id = str(uuid.uuid4())[:8]
        return f"doc_{content_hash}_{unique_id}"

    def _build_index(self, docs_path: Optional[str] = None):
        """
        Build the index

        Flow:
        1. Read documents -> unroll tables
        2. Parent splitting -> InMemoryByteStore
        3. Child splitting -> Chroma + BM25
        """
        path = docs_path or self.docs_path

        if not path:
            raise ValueError("docs_path is required for building index")

        # Ensure the path is a file path
        if os.path.isdir(path):
            # Find cleaned documents
            doc_files = list(Path(path).glob("*_cleaned.md"))
            if not doc_files:
                raise ValueError(f"No cleaned markdown files found in {path}")
            path = str(doc_files[0])  # Use the first cleaned file
        elif '*' in path:
            # Handle wildcard paths (e.g. "knowledge/docs/*_cleaned.md")
            parent_dir = os.path.dirname(path)
            pattern = os.path.basename(path)
            doc_files = list(Path(parent_dir).glob(pattern))
            if not doc_files:
                raise ValueError(f"No cleaned markdown files found matching {path}")
            path = str(doc_files[0])  # Use the first matching file

        print(f"[AdvancedRAG] Loading document: {path}")

        # 1. Read the document
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        print(f"[AdvancedRAG] Original length: {len(content)} chars")

        # 2. Unroll tables
        content = self.table_unroller.process_markdown(content)
        print(f"[AdvancedRAG] Tables processed: {self.table_unroller.stats}")
        print(f"[AdvancedRAG] After table unrolling: {len(content)} chars")

        # 3. Parent splitting (MarkdownHeaderTextSplitter)
        headers_to_split_on = [
            ("#", "H1"),
            ("##", "H2"),
            ("###", "H3"),
        ]

        header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=headers_to_split_on,
            return_each_line=False,
        )

        parent_docs = header_splitter.split_text(content)
        print(f"[AdvancedRAG] Parent docs created: {len(parent_docs)}")

        # 4. Generate doc_id for Parents and store them in the persistent store
        # DocumentPersistentStore provides persistence
        key_value_pairs = []

        for doc in parent_docs:
            doc_id = self._generate_doc_id(doc.page_content)
            doc.metadata['doc_id'] = doc_id
            doc.metadata['source'] = Path(path).name
            key_value_pairs.append((doc_id, doc))

        # Batch write to the persistent store
        self.parent_store.mset(key_value_pairs)

        print(f"[AdvancedRAG] Parent docs stored: {len(self.parent_store)}")

        # 5. Child splitting (RecursiveCharacterTextSplitter)
        child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.child_chunk_size,
            chunk_overlap=self.child_chunk_overlap,
            length_function=len,
            is_separator_regex=False,
        )

        # Split Children out of each Parent
        child_docs: List[Document] = []

        for parent_doc in parent_docs:
            parent_id = parent_doc.metadata['doc_id']

            # Get the header context of the current document
            h1 = parent_doc.metadata.get('H1', '')
            h2 = parent_doc.metadata.get('H2', '')
            h3 = parent_doc.metadata.get('H3', '')

            # Build the header context string
            header_context = " > ".join(filter(None, [h1, h2, h3]))

            # Split
            chunks = child_splitter.split_text(parent_doc.page_content)

            for chunk in chunks:
                child_doc = Document(
                    page_content=chunk,
                    metadata={
                        'parent_id': parent_id,
                        'header_context': header_context,
                        'H1': h1,
                        'H2': h2,
                        'H3': h3,
                        'source': parent_doc.metadata.get('source', ''),
                    }
                )
                child_docs.append(child_doc)

        print(f"[AdvancedRAG] Child docs created: {len(child_docs)}")

        # 6. Build the Chroma vector index (Dense)
        # Ensure the directory exists
        os.makedirs(self.chroma_persist_dir, exist_ok=True)

        self.vectorstore = Chroma.from_documents(
            documents=child_docs,
            embedding=self.embeddings,
            collection_name=self.collection_name,
            persist_directory=self.chroma_persist_dir,
        )

        print(f"[AdvancedRAG] Chroma vectorstore built")

        # 7. Build the BM25 sparse index
        # BM25Retriever requires a document list
        self.bm25_retriever = BM25Retriever.from_documents(
            child_docs,
            preprocess_func=self._bm25_preprocess,
        )

        print(f"[AdvancedRAG] BM25 retriever built")

        self._indexed = True

    def _bm25_preprocess(self, text: str) -> List[str]:
        """BM25 preprocessing: simple tokenization"""
        # Simple tokenization, split by whitespace and punctuation
        import re
        text = text.lower()
        tokens = re.findall(r'\b\w+\b', text)
        return tokens

    def _dense_search(self, query: str, top_k: int = 5) -> List[Document]:
        """
        Dense retrieval: uses MultiVectorRetriever logic

        Since LangChain's MultiVectorRetriever requires a special ByteStore implementation,
        we use a simplified approach: retrieve Children directly with Chroma, then fetch Parents.
        """
        if not self._indexed:
            raise ValueError("Index not built. Call _build_index() first.")

        # Retrieve Children with Chroma
        child_results = self.vectorstore.similarity_search(
            query,
            k=top_k * 2,  # Retrieve more Children to cover more Parents
        )

        # Get Parents via the Children's parent_id
        parent_ids = set()
        for child in child_results:
            parent_id = child.metadata.get('parent_id')
            if parent_id:
                parent_ids.add(parent_id)

        # Fetch Parent documents
        parent_docs = []
        for pid in parent_ids:
            if pid in self.parent_store:
                parent_docs.append(self.parent_store._store[pid])

        return parent_docs

    def _sparse_search(self, query: str, top_k: int = 5) -> List[Document]:
        """
        Sparse retrieval: uses BM25

        BM25 retrieves Children, then fetches Parents via the parent_id in metadata
        """
        if not self._indexed:
            raise ValueError("Index not built. Call _build_index() first.")

        # Set the number of results
        self.bm25_retriever.k = top_k * 2

        # Retrieve Children with BM25
        child_results = self.bm25_retriever.invoke(query)

        # Get Parents via the Children's parent_id
        parent_ids = set()
        for child in child_results:
            parent_id = child.metadata.get('parent_id')
            if parent_id:
                parent_ids.add(parent_id)

        # Fetch Parent documents
        parent_docs = []
        for pid in parent_ids:
            if pid in self.parent_store:
                parent_docs.append(self.parent_store._store[pid])

        return parent_docs

    def _rerank(
        self,
        query: str,
        candidate_docs: List[Document],
        top_k: int = 3
    ) -> List[Document]:
        """
        Jina Reranker reranking

        Scores and sorts the candidate documents with the local Jina Reranker,
        using the model's built-in rerank() method (supports trust_remote_code)
        """
        if not candidate_docs:
            return []

        # Without a reranking model, return the first top_k documents directly
        if not self.reranker_model:
            return candidate_docs[:top_k]

        # Extract the document contents
        doc_contents = [doc.page_content for doc in candidate_docs]

        # Use the rerank method built into the Jina model
        try:
            rerank_results = self.reranker_model.rerank(
                query=query,
                documents=doc_contents,
                top_n=top_k,
            )

            # Extract the sorted document indices from the results
            if isinstance(rerank_results, list) and len(rerank_results) > 0:
                # Sort by relevance_score in descending order
                sorted_indices = [r['index'] for r in rerank_results]
                reranked_docs = [candidate_docs[idx] for idx in sorted_indices]
                return reranked_docs
            else:
                # fallback
                return candidate_docs[:top_k]

        except Exception as e:
            print(f"[AdvancedRAG] Reranking failed: {e}")
            # fallback: return the first top_k documents in their original order
            return candidate_docs[:top_k]

    def hybrid_search_and_rerank(
        self,
        query: str,
        top_k: int = 3,
        dense_weight: float = 0.5,
    ) -> List[Document]:
        """
        Hybrid retrieval + reranking

        Flow:
        1. Dense retrieval (Chroma) -> recalls Top K Parents
        2. Sparse retrieval (BM25) -> recalls Top K Parents
        3. Merge and deduplicate -> candidate Parent list
        4. CrossEncoder reranking -> final Top K

        Args:
            query: query text
            top_k: final number of documents returned
            dense_weight: Dense retrieval weight (currently unused, reserved)

        Returns:
            reranked Top K parent documents
        """
        if not self._indexed:
            # Build the index automatically
            self._build_index()

        print(f"\n[AdvancedRAG] Query: {query}")
        print(f"[AdvancedRAG] Searching...")

        # 1. Dense retrieval
        dense_results = self._dense_search(query, top_k=top_k * 2)
        print(f"[AdvancedRAG] Dense retrieved: {len(dense_results)} parent docs")

        # 2. Sparse retrieval
        sparse_results = self._sparse_search(query, top_k=top_k * 2)
        print(f"[AdvancedRAG] Sparse retrieved: {len(sparse_results)} parent docs")

        # 3. Merge and deduplicate
        seen_ids = set()
        merged_docs = []

        # Add the Dense results first
        for doc in dense_results:
            doc_id = doc.metadata.get('doc_id')
            if doc_id and doc_id not in seen_ids:
                seen_ids.add(doc_id)
                merged_docs.append(doc)

        # Then add the Sparse results (deduplicated)
        for doc in sparse_results:
            doc_id = doc.metadata.get('doc_id')
            if doc_id and doc_id not in seen_ids:
                seen_ids.add(doc_id)
                merged_docs.append(doc)

        print(f"[AdvancedRAG] Merged (deduplicated): {len(merged_docs)} parent docs")

        # 4. Rerank
        reranked_docs = self._rerank(query, merged_docs, top_k=top_k)
        print(f"[AdvancedRAG] After reranking: {len(reranked_docs)} docs")

        return reranked_docs

    def search(self, query: str, top_k: int = 3) -> List[Document]:
        """
        Convenience search method

        Args:
            query: query text
            top_k: number of results

        Returns:
            list of the most relevant documents
        """
        return self.hybrid_search_and_rerank(query, top_k=top_k)


def build_default_rag() -> AdvancedEngineeringRAG:
    """
    Build the RAG system with default configuration

    Returns:
        configured AdvancedEngineeringRAG instance
    """
    # Default document path
    docs_dir = Path(__file__).parent.parent.parent / "knowledge" / "docs"
    docs_path = docs_dir / "MinerU_markdown_Sandvik-TechnicalGuide-MaterialsISO_2067232861531758592_cleaned.md"

    # Vector store directory
    vectorstore_dir = Path(__file__).parent.parent.parent / "knowledge" / "vectorstore"

    rag = AdvancedEngineeringRAG(
        docs_path=str(docs_path),
        reranker_model_path="models",
        chroma_persist_dir=str(vectorstore_dir),
        collection_name="sandvik_materials",
        child_chunk_size=250,
        child_chunk_overlap=30,
    )

    return rag


if __name__ == "__main__":
    print("=" * 70)
    print("Advanced Engineering RAG - Local Test")
    print("=" * 70)

    # Check the API Key (uses the enterprise internal API)
    if not os.environ.get("OPENAI_API_KEY"):
        print("\n[ERROR] OPENAI_API_KEY not found in environment.")
        print("Please set it in the project root .env, or with: set OPENAI_API_KEY=your_key")
        print("(Windows CMD) or: export OPENAI_API_KEY=your_key (Linux/Mac)")
        exit(1)

    # Build the RAG system
    print("\n[1] Building RAG index...")
    try:
        rag = build_default_rag()
        rag._build_index()
        print("[1] Index built successfully!")
    except Exception as e:
        print(f"[1] Index build failed: {e}")
        exit(1)

    # Test queries - hitting the table data
    test_queries = [
        "List the MC codes and the corresponding specific cutting force kc1 for ISO P group steel materials?",
        "What are the characteristics and applications of coated hardmetal HC?",
        "What is the difference between CVD and PVD coatings?",
    ]

    print("\n[2] Testing queries...")

    for i, query in enumerate(test_queries, 1):
        print(f"\n{'='*60}")
        print(f"Query {i}: {query}")
        print("=" * 60)

        try:
            results = rag.hybrid_search_and_rerank(query, top_k=2)

            if results:
                print(f"\nTop {len(results)} results:")

                for j, doc in enumerate(results, 1):
                    print(f"\n--- Result {j} ---")
                    print(f"Source: {doc.metadata.get('source', 'unknown')}")
                    print(f"H1: {doc.metadata.get('H1', '')}")
                    print(f"H2: {doc.metadata.get('H2', '')}")
                    print(f"H3: {doc.metadata.get('H3', '')}")
                    print(f"\nContent (first 300 chars):")
                    print(doc.page_content[:300] + "...")
            else:
                print("\nNo results found.")

        except Exception as e:
            print(f"\nQuery failed: {e}")

    print("\n" + "=" * 70)
    print("Test completed!")
    print("=" * 70)
