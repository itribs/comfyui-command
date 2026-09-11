import re

_INPUT_RE = re.compile(r"input_(\d+)")


class RB_CommandInputBundle:
    """Bundle multiple inputs into a single list output for RB_Command.

    Connect this node's output to an RB_Command input slot.
    In the command, use {inputN} for all bundled items (space-separated),
    {inputN_0}, {inputN_1}, ... for individual items,
    and {inputN_count} for the number of items in the bundle.

    Bundle-to-bundle connections are not supported. Connect each bundle
    to a separate input slot of RB_Command instead.
    """

    RETURN_TYPES = ("RB_COMMAND_INPUT_BUNDLE",)
    RETURN_NAMES = ("bundle",)
    FUNCTION = "bundle"
    CATEGORY = "utils/command"
    OUTPUT_NODE = False
    DESCRIPTION = (
        "Bundle multiple inputs into a single list output. "
        "Connect to RB_Command to use {inputN_0}, {inputN_1}, {inputN_count}, etc."
    )

    INPUT_MAX = 20

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": {
                "input_0": (
                    "*",
                    {
                        "tooltip": "Dynamic input slot, connect any type. "
                        "Connecting triggers more slots up to 20"
                    },
                ),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    def bundle(self, **kwargs):
        items = []
        input_keys = [(int(_INPUT_RE.fullmatch(k).group(1)), k) for k in kwargs if _INPUT_RE.fullmatch(k)]
        for _, k in sorted(input_keys):
            v = kwargs[k]
            if v is None:
                continue
            if isinstance(v, list):
                raise ValueError(
                    "Bundle nodes cannot receive another bundle's output. "
                    "Connect each bundle to a separate input slot of RB_Command instead."
                )
            items.append(v)
        return (items,)


class RB_CommandInputBundlePass:
    """Pass through a bundle input unchanged."""

    RETURN_TYPES = ("RB_COMMAND_INPUT_BUNDLE",)
    RETURN_NAMES = ("bundle",)
    FUNCTION = "pass_through"
    CATEGORY = "utils/command"
    OUTPUT_NODE = False
    DESCRIPTION = "Pass through a bundle input unchanged."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": {
                "bundle": ("RB_COMMAND_INPUT_BUNDLE",),
            },
        }

    def pass_through(self, bundle=None):
        return (bundle or [],)