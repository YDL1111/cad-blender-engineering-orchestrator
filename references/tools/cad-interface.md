# CAD interface contract

The CAD adapter must expose or emulate: capability/version query; open/read; controlled create/update; save-as without overwriting unapproved files; export with units/axes/IDs; structured object/dimension read-back; close/release; and error/blocker reporting. Every mutation takes explicit input/output paths, accepted revisions, writer scope, and overwrite policy.

Success requires a structured completion receipt and reopening or querying the saved/exported artifact in a second process, then comparing controlled IDs, properties, paths, and hashes. A zero exit code alone cannot promote a Stage Result. When no verified adapter exists, return a blocker containing capability status, attempted read-only checks, missing operation, affected stage, and recovery options. Do not invent verified CAD support because a CAD application is installed.
