"""
HTML Table Unroller - table serialization/expansion processor
==============================================================

Flattens HTML tables into natural-language paragraphs, solving the lost-header
problem caused by Markdown splitting.

Main features:
1. Parse HTML tables with BeautifulSoup
2. Handle colspan/rowspan spanning cells
3. Convert each row into a natural-language description
4. Replace the original HTML tables with plain text
"""

import re
from typing import List, Tuple, Optional, Dict
from bs4 import BeautifulSoup, Tag


class HTMLTableUnroller:
    """
    HTML table unroller

    Converts <table>...</table> tables into natural-language paragraphs,
    ensuring every row of data stays linked to the correct header after splitting.
    """

    def __init__(self):
        """Initialize the table unroller"""
        self.stats = {
            'tables_found': 0,
            'tables_processed': 0,
            'rows_converted': 0,
        }

    def reset_stats(self):
        """Reset the statistics"""
        self.stats = {
            'tables_found': 0,
            'tables_processed': 0,
            'rows_converted': 0,
        }

    def _parse_table(self, table: Tag) -> Tuple[List[str], List[List[str]]]:
        """
        Parse an HTML table, extracting headers and data rows

        Args:
            table: a BeautifulSoup <table> tag

        Returns:
            Tuple[header list, data row list]
        """
        headers: List[str] = []
        rows: List[List[str]] = []

        # Extract all <th> elements as headers
        th_elements = table.find_all('th')
        if th_elements:
            headers = [th.get_text(strip=True) for th in th_elements]

        # Extract all <tr> elements
        tr_elements = table.find_all('tr')

        for tr_idx, tr in enumerate(tr_elements):
            # Skip the header row (if the headers are in <thead>)
            if tr.find_parent('thead') and headers:
                continue

            # Skip rows with only <th> cells
            cells = tr.find_all(['td', 'th'])
            if not cells:
                continue

            # Check whether the row is all <th> (possibly an extra header row)
            if all(cell.name == 'th' for cell in cells):
                if not headers:
                    headers = [cell.get_text(strip=True) for cell in cells]
                continue

            # Extract the data
            row_data = []
            for cell in cells:
                text = cell.get_text(strip=True)
                row_data.append(text if text else '-')

            rows.append(row_data)

        return headers, rows

    def _parse_table_with_colspan(
        self,
        table: Tag
    ) -> Tuple[List[List[str]], List[List[str]]]:
        """
        Parse complex tables with colspan/rowspan

        Args:
            table: a BeautifulSoup <table> tag

        Returns:
            Tuple[header grid, data row grid]
        """
        # Build a two-dimensional grid
        grid: List[List[Optional[str]]] = []
        header_grid: List[List[Optional[str]]] = []

        tr_elements = table.find_all('tr')

        for tr in tr_elements:
            row: List[Optional[str]] = []
            cells = tr.find_all(['td', 'th'])

            for cell in cells:
                text = cell.get_text(strip=True)
                rowspan = int(cell.get('rowspan', 1))
                colspan = int(cell.get('colspan', 1))

                for _ in range(colspan):
                    row.append(text)

            if tr.find_parent('thead'):
                header_grid.append(row)
            else:
                grid.append(row)

        return header_grid, grid

    def _flatten_complex_headers(
        self,
        header_grid: List[List[str]]
    ) -> List[str]:
        """
        Flatten a complex header grid into a one-dimensional header list

        For multi-row headers (e.g. merged cells), take the bottom-most header names
        """
        if not header_grid:
            return []

        # Take the last row as the main header
        last_header_row = header_grid[-1]

        # Filter None values
        flat_headers = []
        for h in last_header_row:
            if h:
                flat_headers.append(h)
            else:
                flat_headers.append('-')

        return flat_headers

    def _convert_row_to_text(
        self,
        headers: List[str],
        row: List[str]
    ) -> str:
        """
        Convert a data row with its headers into natural language

        Args:
            headers: header list
            row: data row list

        Returns:
            natural-language description string
        """
        if not headers or not row:
            return ""

        # Ensure the row length matches the header length
        pairs: List[str] = []
        min_len = min(len(headers), len(row))

        for i in range(min_len):
            header = headers[i].strip()
            value = row[i].strip() if i < len(row) else '-'

            if header and value and value != '-':
                pairs.append(f"{header}: {value}")

        if pairs:
            return ", ".join(pairs) + "."
        return ""

    def _process_single_table(self, table_html: str) -> str:
        """
        Process a single HTML table

        Args:
            table_html: the table's HTML string

        Returns:
            the converted natural-language text
        """
        soup = BeautifulSoup(table_html, 'html.parser')
        table = soup.find('table')

        if not table:
            return table_html

        self.stats['tables_found'] += 1

        # Try to parse the headers
        headers: List[str] = []
        rows: List[List[str]] = []

        # First try the simple parse
        th_elements = table.find_all('th')
        if th_elements:
            headers = [th.get_text(strip=True) for th in th_elements]

        # Extract all data rows
        for tr in table.find_all('tr'):
            # Skip the header row
            if tr.find_parent('thead') and headers:
                continue

            cells = tr.find_all('td')
            if not cells:
                continue

            row_data = [cell.get_text(strip=True) for cell in cells]
            if any(row_data):  # skip empty rows
                rows.append(row_data)

        # If no headers were found, try to infer them from the first row
        if not headers and rows:
            # First row as headers, data starts from the second row
            headers = rows[0]
            rows = rows[1:]

        # Generate the natural-language paragraphs
        paragraphs: List[str] = []

        for row in rows:
            text = self._convert_row_to_text(headers, row)
            if text:
                paragraphs.append(text)
                self.stats['rows_converted'] += 1

        return "\n".join(paragraphs) if paragraphs else ""

    def process_text(self, text: str) -> str:
        """
        Process all HTML tables in the text

        Args:
            text: text containing HTML tables

        Returns:
            text with HTML tables converted to natural language
        """
        self.reset_stats()

        # Match the <table>...</table> pattern
        table_pattern = r'<table[^>]*>(.*?)</table>'

        def replace_table(match: re.Match) -> str:
            table_html = match.group(0)
            self.stats['tables_processed'] += 1

            result = self._process_single_table(table_html)

            if result:
                return f"\n{result}\n"
            return ""

        # Replace all tables
        result = re.sub(table_pattern, replace_table, text, flags=re.DOTALL | re.IGNORECASE)

        # Clean up the leftover table-related tags
        result = re.sub(r'</?(table|tbody|thead|tfoot|tr)[^>]*>', '', result, flags=re.IGNORECASE)
        result = re.sub(r'</?(td|th)[^>]*>', '', result, flags=re.IGNORECASE)

        return result

    def process_markdown(self, markdown_text: str) -> str:
        """
        Process tables in Markdown text (supports both Markdown and HTML formats)

        1. Process HTML tables first
        2. Then process the Markdown table format

        Args:
            markdown_text: text in Markdown format

        Returns:
            the processed text
        """
        # Step 1: process HTML tables
        result = self.process_text(markdown_text)

        # Step 2: process the Markdown table format
        lines = result.split('\n')
        processed_lines: List[str] = []
        in_table = False
        table_lines: List[str] = []

        for line in lines:
            # Detect the start of a Markdown table
            if re.match(r'^\|.*\|$', line.strip()) and not in_table:
                # Check whether it is a header row (the next line has separators)
                in_table = True
                table_lines = [line]
            elif in_table:
                # Check whether it is a separator row
                if re.match(r'^\|[\s\-:|]+\|$', line.strip()):
                    table_lines.append(line)
                # Check whether it is a data row or continued header
                elif re.match(r'^\|.*\|$', line.strip()):
                    table_lines.append(line)
                else:
                    # Table ended; process the collected lines
                    processed_lines.extend(self._process_markdown_table(table_lines))
                    table_lines = []
                    in_table = False
                    processed_lines.append(line)
            else:
                processed_lines.append(line)

        # Process any leftover table
        if table_lines:
            processed_lines.extend(self._process_markdown_table(table_lines))

        return '\n'.join(processed_lines)

    def _process_markdown_table(self, table_lines: List[str]) -> List[str]:
        """
        Process a Markdown-format table

        Args:
            table_lines: the table's line list

        Returns:
            the converted natural-language paragraph list
        """
        if len(table_lines) < 2:
            return table_lines

        # Parse the headers
        header_line = table_lines[0]
        headers = [h.strip() for h in header_line.split('|') if h.strip()]

        # Skip the separator row
        data_lines = table_lines[2:] if len(table_lines) > 2 else table_lines[1:]

        # Generate the natural-language paragraphs
        paragraphs: List[str] = []

        for data_line in data_lines:
            cells = [c.strip() for c in data_line.split('|') if c.strip()]

            if len(cells) == len(headers):
                text = self._convert_row_to_text(headers, cells)
                if text:
                    paragraphs.append(text)
                    self.stats['rows_converted'] += 1

        return paragraphs if paragraphs else table_lines


def unroll_tables_in_file(input_path: str, output_path: Optional[str] = None) -> dict:
    """
    Process all tables in a file

    Args:
        input_path: input file path
        output_path: output file path (optional, defaults to overwriting the original file)

    Returns:
        processing statistics
    """
    unroller = HTMLTableUnroller()

    # Read the file
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Process the tables
    result = unroller.process_markdown(content)

    # Write the file
    write_path = output_path or input_path
    with open(write_path, 'w', encoding='utf-8') as f:
        f.write(result)

    return unroller.stats.copy()


if __name__ == "__main__":
    # Test example
    test_html = """
    <table>
        <tr><th>HW</th><th>Uncoated hardmetal containing primarily tungsten carbide (WC).</th></tr>
        <tr><td>HT</td><td>Uncoated hardmetal, also called cermet.</td></tr>
        <tr><td>HC</td><td>Hardmetals as above, but coated.</td></tr>
    </table>
    """

    unroller = HTMLTableUnroller()
    result = unroller.process_text(test_html)

    print("=" * 60)
    print("HTML Table Unroller Test")
    print("=" * 60)
    print("\nOriginal HTML:")
    print(test_html)
    print("\nConverted result:")
    print(result)
    print(f"\nStatistics: {unroller.stats}")
