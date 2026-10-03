# Blender interface contract

The Blender adapter must expose or emulate: capability/version/scene query; import/read; controlled presentation creation/update; save-as; export with units/axes/IDs; structured object/dimension/transform read-back; and blocker reporting. Operate on the intended GUI instance and explicit project file; never assume the visible window or last file is the target.

Success requires a structured completion receipt plus scene/object read-back and output inspection by a second process identity. A zero exit code, welcome text, or save notification is not completion evidence. If MCP is registered but the Blender add-on/port or GUI instance is unavailable, report `installed_not_connected` or `blocked`, not success. Preserve CAD-controlled geometry, identify presentation-only objects separately, and record the declared geometry-fidelity level.
