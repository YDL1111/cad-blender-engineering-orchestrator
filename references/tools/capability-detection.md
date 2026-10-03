# Capability detection

Before a live stage, perform read-only checks: enumerate currently callable MCP/tools, locate CAD/Blender executables, identify script/API/GUI interfaces, and query registration or connection state where safe. Do not install software, edit global Codex configuration, alter scenes, or start real project modeling during detection.

Report each capability as `verified_live`, `installed_not_connected`, `declared_not_verified`, `unavailable`, or `unknown`, with command/tool evidence and timestamp. Prefer a structured API or MCP with read-back; then supported application scripting/CLI; use GUI automation only when no safer observable interface exists. Availability of an executable is not proof of a callable engineering interface.

