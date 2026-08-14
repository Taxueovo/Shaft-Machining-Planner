"""RAG interactive management console.

Launch:
    python -m backend.rag          # interactive mode
    python -m backend.rag --help   # view command-line arguments

The interactive menu provides the full RAG management workflow:
    1. View index status
    2. Scan source files
    3. Build the specs (process handbook) index
    4. Build the cases (case base) index
    5. Build all indexes in one step
    6. Retrieval test
    7. Clear indexes
    8. View chunk details
    0. Exit
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

# Silence verbose logging from chromadb and its dependencies
for _lib in ("chromadb", "chromadb.telemetry", "opentelemetry",
             "urllib3", "kubernetes", "grpc", "huggingface_hub"):
    logging.getLogger(_lib).setLevel(logging.WARNING)

from .config import (
    SPECS_DIR, CASES_DIR, CHROMA_DIR,
    COLLECTION_SPECS, COLLECTION_CASES,
    EMBEDDING_MODEL, embedding_available,
)
from .indexer import IndexBuilder
from .retriever import HybridRetriever
from .schemas import IndexStatus, Channel

# ── Optional Rich imports ──
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.live import Live
    from rich.spinner import Spinner
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# ── Source file scanning ──

SPEC_EXTENSIONS = {".md", ".txt", ".rst"}
CASE_EXTENSIONS = {".json"}


def _scan_spec_files() -> list[Path]:
    if not SPECS_DIR.exists():
        return []
    return sorted(
        f for f in SPECS_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in SPEC_EXTENSIONS
    )


def _scan_case_files() -> list[Path]:
    if not CASES_DIR.exists():
        return []
    return sorted(
        f for f in CASES_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in CASE_EXTENSIONS
        and not f.name.startswith(".")
    )


# ═══════════════════════════════════════════════════════════════
# Rich interactive UI
# ═══════════════════════════════════════════════════════════════

class RAGCli:
    """RAG management console - interactive, menu-driven."""

    def __init__(self):
        self.builder = IndexBuilder()
        self.retriever = HybridRetriever(store=self.builder.store)

        if HAS_RICH:
            self.console = Console()
        else:
            self.console = None

    # ── Rendering helpers ──

    def _print(self, *args, **kwargs):
        if self.console:
            self.console.print(*args, **kwargs)
        else:
            print(*args)

    def _rule(self, title: str):
        if self.console:
            self.console.rule(f"[bold cyan]{title}")
        else:
            print(f"\n{'='*60}")
            print(f"  {title}")
            print(f"{'='*60}")

    def _ok(self, msg: str):
        self._print(f"  [green]✓[/] {msg}" if HAS_RICH else f"  ✓ {msg}")

    def _warn(self, msg: str):
        self._print(f"  [yellow]![/] {msg}" if HAS_RICH else f"  ! {msg}")

    def _err(self, msg: str):
        self._print(f"  [red]✗[/] {msg}" if HAS_RICH else f"  ✗ {msg}")

    def _info(self, msg: str):
        self._print(f"  [dim]{msg}[/]" if HAS_RICH else f"  {msg}")

    # ── 1. Dashboard ──

    def show_dashboard(self):
        """Display the index status dashboard."""
        self._rule("📊 RAG Index Dashboard")

        status = self.builder.get_status()
        spec_files = _scan_spec_files()
        case_files = _scan_case_files()

        if HAS_RICH:
            # System info
            info_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
            info_table.add_column("Key", style="dim")
            info_table.add_column("Value")
            info_table.add_row("Embedding model", EMBEDDING_MODEL)
            info_table.add_row("Embedding available",
                               "[green]✓ Configured" if status.embedding_available else "[red]✗ Not configured")
            info_table.add_row("ChromaDB path", str(CHROMA_DIR))
            self._print(info_table)
            self._print()

            # Specs (process handbook)
            spec_table = Table(title="📖 Specs (Process Handbook)", box=box.SIMPLE_HEAVY, padding=(0, 2))
            spec_table.add_column("Metric", style="dim")
            spec_table.add_column("Value", style="bold")
            spec_table.add_row("Source files", str(len(spec_files)))
            spec_table.add_row("Indexed chunks", str(status.specs.document_count))
            spec_table.add_row("Directory", str(SPECS_DIR))
            if spec_files:
                spec_table.add_row("Files", "\n".join(f"  · {f.name}" for f in spec_files))
            else:
                spec_table.add_row("Files", "[dim](empty - drop in .md/.txt files and build)[/]")
            self._print(spec_table)
            self._print()

            # Cases (case base)
            case_table = Table(title="📋 Cases (Case Base)", box=box.SIMPLE_HEAVY, padding=(0, 2))
            case_table.add_column("Metric", style="dim")
            case_table.add_column("Value", style="bold")
            case_table.add_row("Source files", str(len(case_files)))
            case_table.add_row("Indexed chunks", str(status.cases.document_count))
            case_table.add_row("Directory", str(CASES_DIR))
            if case_files:
                case_table.add_row("Files", "\n".join(f"  · {f.name}" for f in case_files))
            else:
                case_table.add_row("Files", "[dim](empty - drop in .json files and build)[/]")
            self._print(case_table)
            self._print()

            # Summary bar
            total_src = len(spec_files) + len(case_files)
            total_idx = status.specs.document_count + status.cases.document_count
            summary = Text()
            summary.append("📦 Total: ", style="dim")
            summary.append(f"{total_src} source files", style="bold yellow")
            summary.append(" | ", style="dim")
            summary.append(f"{total_idx} indexed chunks", style="bold green")
            summary.append(" | ", style="dim")
            if total_idx > 0:
                summary.append("✅ Index ready", style="bold green")
            else:
                summary.append("⏳ Index pending build", style="bold yellow")
            self._print(Panel(summary, box=box.SIMPLE))
        else:
            # Plain-text fallback
            print(f"\n  Embedding model: {EMBEDDING_MODEL}")
            print(f"  Embedding available: {'✓' if status.embedding_available else '✗'}")
            print(f"\n  📖 Specs: {status.specs.document_count} chunks | {len(spec_files)} source files")
            print(f"  📋 Cases: {status.cases.document_count} chunks | {len(case_files)} source files")

    # ── 2. Scan source files ──

    def show_source_files(self):
        """Show the detailed list of source files."""
        self._rule("📁 Source File Scan")

        for label, files, extensions, directory in [
            ("📖 Specs", _scan_spec_files(), SPEC_EXTENSIONS, SPECS_DIR),
            ("📋 Cases", _scan_case_files(), CASE_EXTENSIONS, CASES_DIR),
        ]:
            self._print(f"\n  [bold]{label}[/]" if HAS_RICH else f"\n  {label}")
            self._info(f"  Directory: {directory}")
            self._info(f"  Supported formats: {', '.join(extensions)}")
            if files:
                for f in files:
                    size_kb = f.stat().st_size / 1024
                    self._print(f"    [green]📄 {f.name}[/] [dim]({size_kb:.1f} KB)[/]" if HAS_RICH
                                else f"    📄 {f.name} ({size_kb:.1f} KB)")
            else:
                self._warn(f"  (empty - drop in {'/'.join(extensions)} files and build)")
        print()

    # ── 3. Build the specs index ──

    def build_specs(self):
        """Build the specs (process handbook) index."""
        self._rule("🔨 Build Specs Index")
        files = _scan_spec_files()
        if not files:
            self._warn("No spec source files found. Place .md/.txt files into data/specs/ first.")
            return

        self._info(f"Found {len(files)} source files, building...")
        t0 = time.time()
        try:
            count = self.builder.build_spec_index()
            elapsed = time.time() - t0
            self._ok(f"Done! {count} chunks indexed in {elapsed:.1f}s")
        except Exception as exc:
            self._err(f"Build failed: {exc}")

    # ── 4. Build the cases index ──

    def build_cases(self):
        """Build the cases (case base) index."""
        self._rule("🔨 Build Cases Index")
        files = _scan_case_files()
        if not files:
            self._warn("No case source files found. Place .json files into data/cases/ first.")
            return

        self._info(f"Found {len(files)} source files, building...")
        t0 = time.time()
        try:
            count = self.builder.build_case_index()
            elapsed = time.time() - t0
            self._ok(f"Done! {count} chunks indexed in {elapsed:.1f}s")
        except Exception as exc:
            self._err(f"Build failed: {exc}")

    # ── 5. Build all ──

    def build_all(self):
        """Build both channel indexes in one step."""
        self._rule("🚀 Build All Indexes")

        spec_files = _scan_spec_files()
        case_files = _scan_case_files()
        if not spec_files and not case_files:
            self._warn("No source files in either directory. Add files first:")
            self._info(f"  Specs -> {SPECS_DIR}")
            self._info(f"  Cases -> {CASES_DIR}")
            return

        self._info(f"Specs: {len(spec_files)} files | Cases: {len(case_files)} files")
        self._info("Building...\n")

        t0 = time.time()

        # Specs
        if spec_files:
            try:
                s = self.builder.build_spec_index()
                self._ok(f"📖 Specs: {s} chunks")
            except Exception as exc:
                self._err(f"📖 Specs failed: {exc}")
        else:
            self._info("📖 Specs: no files, skipped")

        # Cases
        if case_files:
            try:
                c = self.builder.build_case_index()
                self._ok(f"📋 Cases: {c} chunks")
            except Exception as exc:
                self._err(f"📋 Cases failed: {exc}")
        else:
            self._info("📋 Cases: no files, skipped")

        elapsed = time.time() - t0
        self._print()
        self._ok(f"All done in {elapsed:.1f}s")

    # ── 6. Retrieval test ──

    def search_test(self):
        """Interactive retrieval test."""
        self._rule("🔍 Retrieval Test")

        status = self.builder.get_status()
        if status.specs.document_count == 0 and status.cases.document_count == 0:
            self._warn("The index is empty; build it first (menu options 3/4/5)")
            return

        self._info("Enter a natural-language query (type 'q' to return to the menu)")
        self._info(f"Current index: specs {status.specs.document_count} | cases {status.cases.document_count}")
        print()

        while True:
            try:
                query = input("  🔍 Query > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not query:
                continue
            if query.lower() == 'q':
                break

            try:
                response = self.retriever.retrieve(query, top_k_per_channel=3)
            except Exception as exc:
                self._err(f"Retrieval failed: {exc}")
                continue

            if not response.results:
                self._warn("No relevant results found. Try different keywords?")
                continue

            print()
            for i, r in enumerate(response.results):
                channel_icon = "📖" if r.channel == Channel.SPECS else "📋"
                channel_name = "Specs" if r.channel == Channel.SPECS else "Cases"

                if HAS_RICH:
                    meta_text = ""
                    if r.channel == Channel.SPECS:
                        meta_text = r.metadata.get("hierarchy_path", "")
                    else:
                        meta_text = f"{r.metadata.get('part_name', '')} ({r.metadata.get('case_id', '')})"

                    self._print(
                        f"  [bold]{i+1}.[/] {channel_icon} "
                        f"[bold cyan]{channel_name}[/] "
                        f"[yellow]score {r.score}[/]"
                    )
                    if meta_text:
                        self._print(f"    [dim]{meta_text}[/]")
                    preview = r.content[:200].replace("\n", " ").strip()
                    self._print(f"    [dim]{preview}...[/]")
                    self._print()
                else:
                    print(f"  {i+1}. [{channel_name}] score {r.score}")
                    print(f"    {r.content[:200]}...")
                    print()

    # ── 7. Clear indexes ──

    def clear_indexes(self):
        """Clear the indexes."""
        self._rule("🧹 Clear Indexes")
        status = self.builder.get_status()

        if status.specs.document_count == 0 and status.cases.document_count == 0:
            self._warn("The index is already empty; nothing to clear")
            return

        print()
        self._warn(f"⚠️  This will delete all index data:")
        self._info(f"  Specs: {status.specs.document_count} chunks")
        self._info(f"  Cases: {status.cases.document_count} chunks")
        print()

        confirm = input("  Confirm clear? Type 'yes' to continue: ").strip()
        if confirm.lower() != 'yes':
            self._info("Cancelled")
            return

        self.builder.store.clear()
        self._ok("All indexes cleared")

    # ── 8. Chunk details ──

    def show_chunks(self):
        """Show samples of indexed chunks."""
        self._rule("📋 Chunk Details")
        status = self.builder.get_status()

        if status.specs.document_count == 0 and status.cases.document_count == 0:
            self._warn("The index is empty; build it first")
            return

        # Fetch sample data from ChromaDB
        for col_name, label, icon in [
            (COLLECTION_SPECS, "Specs", "📖"),
            (COLLECTION_CASES, "Cases", "📋"),
        ]:
            col_status = self.builder.store.get_collection_status(col_name)
            if col_status["document_count"] == 0:
                continue

            self._print(f"\n  [bold]{icon} {label} ({col_status['document_count']} chunks)[/]" if HAS_RICH
                        else f"\n  {icon} {label} ({col_status['document_count']} chunks)")

            col = (self.builder.store.specs_collection if col_name == COLLECTION_SPECS
                   else self.builder.store.cases_collection)

            try:
                data = col.get(limit=5, include=["documents", "metadatas"])
            except Exception as exc:
                self._err(f"Read failed: {exc}")
                continue

            for i, (cid, doc, meta) in enumerate(
                zip(data.get("ids", []), data.get("documents", []), data.get("metadatas", []))
            ):
                if HAS_RICH:
                    self._print(f"\n  [bold dim]#{i+1} {cid}[/]")
                    if col_name == COLLECTION_SPECS:
                        path = meta.get("hierarchy_path", "") if meta else ""
                        if path:
                            self._print(f"  [cyan]Hierarchy:[/] {path}")
                    else:
                        case_label = f"{meta.get('part_name', '')} ({meta.get('case_id', '')})" if meta else ""
                        if case_label:
                            self._print(f"  [cyan]Case:[/] {case_label}")
                    preview = doc[:300].replace("\n", " ").strip() if doc else ""
                    self._print(f"  [dim]{preview}...[/]")
                else:
                    print(f"\n  #{i+1} {cid}")
                    print(f"  {doc[:300] if doc else ''}...")
            print()

    # ── Menu loop ──

    def run(self):
        """Start the interactive menu loop."""
        self._clear_screen()
        self._print_header()
        self.show_dashboard()

        while True:
            self._print_menu()
            try:
                choice = input("  Choose [0-8] > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            self._clear_screen()
            self._print_header()

            if choice == "1":
                self.show_dashboard()
            elif choice == "2":
                self.show_source_files()
            elif choice == "3":
                self.build_specs()
            elif choice == "4":
                self.build_cases()
            elif choice == "5":
                self.build_all()
            elif choice == "6":
                self.search_test()
            elif choice == "7":
                self.clear_indexes()
            elif choice == "8":
                self.show_chunks()
            elif choice == "0":
                self._print("\n  👋 Goodbye!\n" if HAS_RICH else "\n  Goodbye!\n")
                break
            else:
                self._warn(f"Invalid option: '{choice}'. Enter 0-8")

    def _clear_screen(self):
        os.system("cls" if os.name == "nt" else "clear")

    def _print_header(self):
        if HAS_RICH:
            title = Panel(
                "[bold white]ShaftPlanner RAG Management Console[/]\n"
                "[dim]Dual-channel differentiated chunks | Specs + Cases | ChromaDB[/]",
                box=box.DOUBLE,
                border_style="cyan",
            )
            self._print(title)
        else:
            print("\n" + "=" * 60)
            print("  ShaftPlanner RAG Management Console")
            print("  Dual-channel differentiated chunks | Specs + Cases")
            print("=" * 60)

    def _print_menu(self):
        print()
        if HAS_RICH:
            menu = Table(box=box.SIMPLE_HEAVY, show_header=False, padding=(0, 2))
            menu.add_column("Key", style="bold cyan", width=4)
            menu.add_column("Action")
            menu.add_column("Description", style="dim")
            menu.add_row("1", "📊 View dashboard", "Index status overview")
            menu.add_row("2", "📁 Scan source files", "List files pending indexing")
            menu.add_row("3", "🔨 Build specs index", "chunk -> embed -> write to ChromaDB")
            menu.add_row("4", "🔨 Build cases index", "chunk -> embed -> write to ChromaDB")
            menu.add_row("5", "🚀 Build all", "Build specs and cases together")
            menu.add_row("6", "🔍 Retrieval test", "Run a query to test retrieval")
            menu.add_row("7", "🧹 Clear indexes", "Delete all vector data")
            menu.add_row("8", "📋 Chunk details", "View samples of indexed chunks")
            menu.add_row("0", "Exit", "")
            self._print(menu)
        else:
            print("  ┌──────────────────────────────────────────┐")
            print("  │ 1. View dashboard    5. Build all        │")
            print("  │ 2. Scan source files 6. Retrieval test   │")
            print("  │ 3. Build specs       7. Clear indexes    │")
            print("  │ 4. Build cases       8. Chunk details    │")
            print("  │                       0. Exit             │")
            print("  └──────────────────────────────────────────┘")


# ═══════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════

def main():
    """CLI entry point - supports interactive mode and command-line arguments."""
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        print("Command-line shortcuts:")
        print("  --dashboard    show the dashboard and exit")
        print("  --scan         scan source files and exit")
        print("  --build-all    build all indexes and exit")
        print("  --build-specs  build the specs index and exit")
        print("  --build-cases  build the cases index and exit")
        print("  --search TEXT  run a retrieval query and exit")
        print("  --clear        clear all indexes and exit")
        print("  --chunks       show chunk details and exit")
        print("  (no argument)  enter interactive mode")
        sys.exit(0)

    cli = RAGCli()

    # ── Command-line shortcut mode ──
    if "--dashboard" in sys.argv:
        cli.show_dashboard()
        sys.exit(0)
    if "--scan" in sys.argv:
        cli.show_source_files()
        sys.exit(0)
    if "--build-all" in sys.argv:
        cli.build_all()
        sys.exit(0)
    if "--build-specs" in sys.argv:
        cli.build_specs()
        sys.exit(0)
    if "--build-cases" in sys.argv:
        cli.build_cases()
        sys.exit(0)
    if "--search" in sys.argv:
        idx = sys.argv.index("--search")
        if idx + 1 < len(sys.argv):
            query = sys.argv[idx + 1]
            cli._rule(f"Retrieval: {query}")
            response = cli.retriever.retrieve(query)
            for i, r in enumerate(response.results):
                channel = "📖Specs" if r.channel == Channel.SPECS else "📋Cases"
                print(f"\n{i+1}. [{channel}] score {r.score}")
                print(f"   {r.content[:300]}...")
            if not response.results:
                print("No results found.")
        else:
            print("Usage: --search 'query text'")
        sys.exit(0)
    if "--clear" in sys.argv:
        cli.clear_indexes()
        sys.exit(0)
    if "--chunks" in sys.argv:
        cli.show_chunks()
        sys.exit(0)

    # ── Interactive mode ──
    try:
        cli.run()
    except KeyboardInterrupt:
        print("\n\n  Exited.\n")


if __name__ == "__main__":
    main()
