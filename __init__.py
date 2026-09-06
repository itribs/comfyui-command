import os

from .command import RB_Command

NODE_CLASS_MAPPINGS = {
    "RB_Command": RB_Command
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RB_Command": "RB Command"
}

WEB_DIRECTORY = os.path.join(os.path.dirname(__file__), "web")

__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']