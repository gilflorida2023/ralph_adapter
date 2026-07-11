NAME = "strip_markdown_fences"
DESCRIPTION = "Remove ```json ... ``` code fences from model output"


def normalize(text):
    """Remove markdown code fence markers."""
    for fence in ("```json", "```"):
        text = text.replace(fence, "")
    return text.strip()
