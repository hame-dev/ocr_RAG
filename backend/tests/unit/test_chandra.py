"""Chandra's page splitting.

The CLI hands back one merged markdown blob with a separator between pages, so
this split is the only thing reconstructing per-page structure. If it silently
mis-splits, every downstream page mapping and citation is wrong — hence the
test builds its input with the CLI's own writer logic rather than a guess.
"""
from ocr.engines.chandra import split_pages


def merged_like_cli(pages: list[str]) -> str:
    """Reproduce chandra/scripts/cli.py::save_merged_output with paginate on."""
    parts: list[str] = []
    for index, page in enumerate(pages):
        if index > 0:
            parts.append(f"\n\n{index}" + "-" * 48 + "\n\n")
        parts.append(page)
    return "".join(parts)


def test_splits_on_the_cli_page_separator():
    pages = ["# One\nalpha", "## Two\nbeta", "Three"]

    assert split_pages(merged_like_cli(pages)) == pages


def test_single_page_document_is_not_split():
    assert split_pages("just one page") == ["just one page"]


def test_markdown_table_rule_is_not_a_page_break():
    # A table's |---|---| row and a plain horizontal rule both contain runs of
    # dashes; neither starts with digits, so neither may split a page.
    table = "| a | b |\n|---|---|\n| 1 | 2 |"
    assert split_pages(table) == [table]

    rule = "text\n" + "-" * 60 + "\nmore text"
    assert split_pages(rule) == [rule]


def test_empty_output_yields_no_pages():
    assert split_pages("") == []


def test_arabic_content_survives_the_split():
    pages = ["عقد إيجار سنوي", "الطرف الأول: شركة النور"]
    assert split_pages(merged_like_cli(pages)) == pages
