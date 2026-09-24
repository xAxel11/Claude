from . import code_tools, email_tools, instagram, memory, screen, system, vision_tools, web

ALL_TOOLS = (
    screen.TOOLS
    + system.TOOLS
    + code_tools.TOOLS
    + web.TOOLS
    + vision_tools.TOOLS
    + email_tools.TOOLS
    + instagram.TOOLS
    + memory.TOOLS
)
