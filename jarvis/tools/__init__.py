from . import email_tools, instagram, memory, system, vision_tools, web

ALL_TOOLS = (
    system.TOOLS + web.TOOLS + vision_tools.TOOLS + email_tools.TOOLS + instagram.TOOLS + memory.TOOLS
)
