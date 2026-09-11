import os

from .nodes.command import RB_Command
from .nodes.input_bundle import RB_CommandInputBundle, RB_CommandInputBundlePass

NODE_CLASS_MAPPINGS = {
    "RB_Command": RB_Command,
    "RB_CommandInputBundle": RB_CommandInputBundle,
    "RB_CommandInputBundlePass": RB_CommandInputBundlePass,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RB_Command": "RB Command",
    "RB_CommandInputBundle": "RB Command Input Bundle",
    "RB_CommandInputBundlePass": "RB Command Input Bundle Pass",
}

WEB_DIRECTORY = os.path.join(os.path.dirname(__file__), "web")

__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']