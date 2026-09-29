"""Applies the local patches to the ref-seeker-extractor source inside the image build.

Kept as a file rather than inlined into Dockerfile.local because a heredoc there picks up
the checkout's CRLF line endings and fails to parse. See empty-datastorage.patch for details.
"""
import pathlib
import sys

# A datastorage with no attributes of its own (a nested document path whose children are all
# objects) reaches get_max_entry_number with an empty list, which becomes the invalid `IN ()`
# and aborts the job. MMSBench-Net's log documents contain such paths.
UAC = pathlib.Path("services/UniqueAttributecombinations.py")
UAC_ANCHOR = "        attributes_to_remove = []\n\n        # Determine number of entries"
UAC_GUARD = (
    "        attributes_to_remove = []\n\n"
    "        # A datastorage with no attributes -- a nested document path whose children\n"
    "        # are all objects -- has no unique attribute combination to find. Without\n"
    "        # this the empty list becomes `IN ()`, a syntax error, and the job aborts.\n"
    "        if not attributes:\n"
    "            return\n\n"
    "        # Determine number of entries"
)


def main():
    text = UAC.read_text()
    if "if not attributes:" in text:
        print("UACFinder guard already present")
        return 0
    if text.count(UAC_ANCHOR) != 1:
        print("UACFinder guard: anchor not found, refusing to patch", file=sys.stderr)
        return 1
    UAC.write_text(text.replace(UAC_ANCHOR, UAC_GUARD, 1))
    print("UACFinder guard applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
