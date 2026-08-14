"""
Markdown Cleaner - deep cleaning of MinerU-generated Markdown files
===================================================================

Removes noise caused by PDF physical layout, repairs semantic breaks, and
prepares the content for RAG chunking.

Main features:
1. Remove image placeholders (![image](...))
2. Strip headers, footers and page breaks
3. Repair semantic breaks caused by hard line wraps
4. Strip table-of-contents blocks
5. Clean up extra blank lines
"""

import re
import os
from pathlib import Path
from typing import Optional, List, Tuple


class MarkdownCleaner:
    """
    Deep cleaner for Markdown documents

    Cleans noise elements from Markdown files converted from PDF by MinerU.
    """

    # Brand/copyright word list (may include spelling variants)
    BRAND_WORDS = [
        'SANDVIK', 'Coromant', 'Coromani', 'Coromant.',
        'SANDVIK COROMANT', 'SANDVIK\\s*COROMANT',
        r'S\.?A\.?N\.?D\.?V\.?I\.?K',  # handles possible spelling variants
    ]

    def __init__(self):
        """Initialize the cleaner"""
        self.stats = {
            'images_removed': 0,
            'page_breaks_removed': 0,
            'brand_lines_removed': 0,
            'toc_lines_removed': 0,
            'lines_joined': 0,
        }

    def reset_stats(self):
        """Reset the statistics"""
        self.stats = {
            'images_removed': 0,
            'page_breaks_removed': 0,
            'brand_lines_removed': 0,
            'toc_lines_removed': 0,
            'lines_joined': 0,
        }

    def remove_image_tags(self, content: str) -> str:
        """
        Remove image placeholders

        Deletes all Markdown image syntax: ![alt](url) and ![alt][ref] formats
        """
        # Match the ![alt](url) format
        pattern1 = r'!\[([^\]]*)\]\([^\)]+\)'

        # Match the ![alt][ref] format (reference-style images)
        pattern2 = r'!\[([^\]]*)\]\[[^\]]+\]'

        # Match bare image tags like ![image] or ![]()
        pattern3 = r'!\[[^\]]*\]'

        result = re.sub(pattern1, '', content)
        result = re.sub(pattern2, '', result)
        result = re.sub(pattern3, '', result)

        # Count the removed images
        images_found = len(re.findall(r'!\[[^\]]*\]', content))
        self.stats['images_removed'] += images_found

        return result

    def remove_headers_footers_pagebreaks(self, content: str) -> str:
        """
        Strip headers, footers and page breaks

        Deletes:
        - Page breaks: --- PAGE X ---, Page X, etc.
        - Copyright/brand words: SANDVIK, Coromant, etc.
        """
        lines = content.split('\n')
        cleaned_lines = []

        # Page break regex patterns
        page_break_patterns = [
            r'^[-=_*]{3,}\s*PAGE\s*\d+\s*[-=_*]{3,}$',  # --- PAGE 1 ---
            r'^[-=_*]{3,}\s*Page\s*\d+\s*[-=_*]{3,}$',  # --- Page 1 ---
            r'^\s*[-=_*]{3,}\s*$',  # plain separator line
            r'^--- PAGE \d+ ---$',
            r'^Page \d+$',
        ]

        # Merge the brand word patterns
        brand_pattern = '|'.join(self.BRAND_WORDS)
        brand_line_pattern = rf'^\s*({brand_pattern})[\s\d\.\-\–\—]*\s*$'

        for line in lines:
            original_line = line
            is_noise = False

            # Check whether it is a page break
            for pattern in page_break_patterns:
                if re.match(pattern, line.strip(), re.IGNORECASE):
                    self.stats['page_breaks_removed'] += 1
                    is_noise = True
                    break

            # Check whether it is a brand/copyright line
            if not is_noise and re.match(brand_line_pattern, line, re.IGNORECASE):
                self.stats['brand_lines_removed'] += 1
                is_noise = True

            # Check for an isolated brand word (a whole paragraph consisting only of brand words)
            if not is_noise:
                clean_line = re.sub(r'\s+', ' ', line.strip())
                if re.match(brand_line_pattern, clean_line, re.IGNORECASE):
                    self.stats['brand_lines_removed'] += 1
                    is_noise = True

            if not is_noise:
                cleaned_lines.append(line)

        return '\n'.join(cleaned_lines)

    def remove_toc_blocks(self, content: str) -> str:
        """
        Strip table-of-contents blocks

        Detects text blocks that continuously match the "section heading + page number" pattern
        e.g.:
        H 1 Introduction .................. 1
        H 2 Installation ................ 5
        """
        lines = content.split('\n')
        cleaned_lines = []
        i = 0

        while i < len(lines):
            line = lines[i]

            # TOC line pattern: H + number + heading + page number (dots or digits)
            # e.g.: H 1 Introduction ............... 1
            # e.g.: H 2.3 Installation Guide........ 12
            toc_pattern = r'^H\s+\d+(\.\d+)*\s+[\w\s\-\(\)\/]+[\.\s]+(\d+|[ivxIVX]+)\s*$'

            # Alternative pattern: simple page-number format
            simple_toc_pattern = r'^H\s+\d+[\.\d]*\s+\S+.*?\d+\s*$'

            is_toc_line = False

            if re.match(toc_pattern, line, re.IGNORECASE) or re.match(simple_toc_pattern, line):
                # Check whether the following lines are also in TOC format (consecutive detection)
                toc_count = 0
                j = i
                while j < len(lines) and j < i + 50:  # check at most 50 lines
                    next_line = lines[j]
                    if (re.match(toc_pattern, next_line, re.IGNORECASE) or
                            re.match(simple_toc_pattern, next_line)):
                        toc_count += 1
                        j += 1
                    elif next_line.strip() == '':
                        j += 1  # skip blank lines
                    else:
                        break

                # If multiple consecutive TOC lines are detected, remove the whole TOC block
                if toc_count >= 3:
                    self.stats['toc_lines_removed'] += toc_count
                    i = j
                    continue
                else:
                    is_toc_line = True

            if not is_toc_line:
                cleaned_lines.append(line)

            i += 1

        return '\n'.join(cleaned_lines)

    def fix_hard_line_breaks(self, content: str) -> str:
        """
        Repair semantic breaks caused by hard line wraps

        Stitches together sentences truncated within the same paragraph:
        - If a line does not end with a punctuation mark
        - and is not a Markdown structural marker (#, -, *, |, numbered list)
        - and the next line is immediately followed by text
        then the line break between the two lines is replaced with a space
        """
        lines = content.split('\n')
        result_lines = []

        i = 0
        while i < len(lines):
            line = lines[i]

            # Check whether it should be merged with the next line
            should_join = False

            if i + 1 < len(lines):
                next_line = lines[i + 1]

                # Check whether the current line ends with a punctuation mark
                # Punctuation: . ! ? : ; ,
                ends_with_punctuation = bool(
                    re.search(r'[\.\!\?\,\:\;]$', line)
                )

                # Check whether the next line is a Markdown structural marker
                # # heading, - list, * list, | table, numbered list (1. 2. or 1)
                # as well as blank lines
                is_structured = bool(
                    re.match(r'^\s*[#\*\-\+\>\|\d]+\s', next_line) or
                    next_line.strip() == ''
                )

                # Check whether the next line is unstructured text (actual content)
                is_continuation = bool(
                    next_line.strip() and
                    not is_structured and
                    not next_line.strip().startswith('#')
                )

                # Condition: the current line does not end with punctuation and the next line is continuation text
                if not ends_with_punctuation and is_continuation:
                    should_join = True

            if should_join:
                # Merge the two lines with a space in between
                merged_line = line.rstrip() + ' ' + lines[i + 1].strip()
                result_lines.append(merged_line)
                self.stats['lines_joined'] += 1
                i += 2  # skip the next line (already merged)
            else:
                result_lines.append(line)
                i += 1

        return '\n'.join(result_lines)

    def remove_extra_blank_lines(self, content: str) -> str:
        """
        Clean up extra blank lines

        Compresses runs of more than 2 consecutive blank lines into 2
        """
        # Replace 3 or more consecutive blank lines with 2
        result = re.sub(r'\n{3,}', '\n\n', content)
        # Remove trailing whitespace on lines
        result = re.sub(r' +\n', '\n', result)
        return result

    def clean_text(self, content: str) -> str:
        """
        Run the full cleaning pipeline

        Executes in order:
        1. Remove images
        2. Remove headers, footers and page breaks
        3. Remove table of contents
        4. Stitch line wraps
        5. Clean up extra blank lines
        """
        self.reset_stats()

        # 1. Remove image placeholders
        content = self.remove_image_tags(content)

        # 2. Strip headers, footers and page breaks
        content = self.remove_headers_footers_pagebreaks(content)

        # 3. Strip table-of-contents blocks
        content = self.remove_toc_blocks(content)

        # 4. Repair semantic breaks caused by hard line wraps
        content = self.fix_hard_line_breaks(content)

        # 5. Clean up extra blank lines
        content = self.remove_extra_blank_lines(content)

        # Strip leading/trailing whitespace
        content = content.strip()

        return content

    def clean_file(self, input_path: str, output_path: str) -> Tuple[int, int, dict]:
        """
        Clean a Markdown file

        Args:
            input_path: input file path
            output_path: output file path

        Returns:
            Tuple[original char count, cleaned char count, statistics]
        """
        # Read the input file
        with open(input_path, 'r', encoding='utf-8') as f:
            content = f.read()

        original_length = len(content)

        # Run the cleaning
        cleaned_content = self.clean_text(content)

        cleaned_length = len(cleaned_content)

        # Write the output file
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(cleaned_content)

        return original_length, cleaned_length, self.stats.copy()


def find_markdown_files(docs_dir: str, pattern: str = "MinerU_markdown_*.md") -> List[str]:
    """
    Find Markdown files in the knowledge/docs directory

    Args:
        docs_dir: document directory path
        pattern: file matching pattern

    Returns:
        list of file paths
    """
    docs_path = Path(docs_dir)
    if not docs_path.exists():
        return []

    return sorted(docs_path.glob(pattern))


def clean_all_documents(docs_dir: str, output_suffix: str = "_cleaned") -> dict:
    """
    Clean all matching Markdown files in a directory

    Args:
        docs_dir: document directory
        output_suffix: output file suffix

    Returns:
        cleaning statistics summary
    """
    files = find_markdown_files(docs_dir)

    if not files:
        print(f"No matching files found in {docs_dir}")
        return {}

    cleaner = MarkdownCleaner()
    summary = {
        'total_files': len(files),
        'files_processed': [],
        'total_original_chars': 0,
        'total_cleaned_chars': 0,
    }

    print(f"\n{'='*60}")
    print(f"Start cleaning {len(files)} files")
    print(f"{'='*60}\n")

    for input_file in files:
        # Build the output path
        output_name = input_file.stem + output_suffix + '.md'
        output_file = input_file.parent / output_name

        print(f"Processing: {input_file.name}")
        print(f"  -> {output_file.name}")

        try:
            orig_len, clean_len, stats = cleaner.clean_file(
                str(input_file),
                str(output_file)
            )

            removed_ratio = (1 - clean_len / orig_len) * 100 if orig_len > 0 else 0

            print(f"  Original: {orig_len:,} chars")
            print(f"  Cleaned: {clean_len:,} chars")
            print(f"  Removed ratio: {removed_ratio:.1f}%")
            print(f"  Statistics: {stats}")
            print()

            summary['files_processed'].append({
                'file': input_file.name,
                'original_chars': orig_len,
                'cleaned_chars': clean_len,
                'stats': stats,
            })
            summary['total_original_chars'] += orig_len
            summary['total_cleaned_chars'] += clean_len

        except Exception as e:
            print(f"  Error: {e}\n")

    return summary


if __name__ == "__main__":
    # Configuration paths
    DOCS_DIR = Path(__file__).parent.parent.parent / "knowledge" / "docs"
    OUTPUT_SUFFIX = "_cleaned"

    print("\n" + "="*60)
    print("Markdown Deep Cleaning Tool")
    print("="*60)
    print(f"Docs directory: {DOCS_DIR}")
    print()

    # Find the files to clean
    files = find_markdown_files(str(DOCS_DIR))

    if files:
        print(f"Found {len(files)} files to clean:\n")
        for f in files:
            print(f"  - {f.name}")
        print()

        # Run the cleaning
        summary = clean_all_documents(str(DOCS_DIR), OUTPUT_SUFFIX)

        # Print the summary
        if summary.get('total_files', 0) > 0:
            print("="*60)
            print("Cleaning Summary")
            print("="*60)
            print(f"Files processed: {summary['total_files']}")
            print(f"Total original chars: {summary['total_original_chars']:,}")
            print(f"Total cleaned chars: {summary['total_cleaned_chars']:,}")

            total_removed = summary['total_original_chars'] - summary['total_cleaned_chars']
            total_ratio = (total_removed / summary['total_original_chars'] * 100
                           if summary['total_original_chars'] > 0 else 0)
            print(f"Chars removed: {total_removed:,} ({total_ratio:.1f}%)")
    else:
        print("No files found to clean")
        print(f"\nTip: place the MinerU-generated Markdown files in:")
        print(f"  {DOCS_DIR}")
        print(f"\nFile names should match the pattern: MinerU_markdown_*.md")
