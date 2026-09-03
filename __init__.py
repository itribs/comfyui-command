from .command_node import CommandNode

NODE_CLASS_MAPPINGS = {
    "CommandNode": CommandNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CommandNode": "Command Node"
}

# Set web directory for frontend JS extension support
WEB_DIRECTORY = "./web"

__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']