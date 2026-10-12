# Rule Plugin Example

`minimum_text_length.py` demonstrates a rule that is discovered at application startup without edits to the API, wizard, dashboard, or standard registry JSON. A plugin provides its rule metadata, applicability evidence, an initial configuration, deterministic validation, and failure explanation. Rule configuration metadata drives generic form controls in the UI.

To add a rule, create a Python module in this folder with a `RULE_PLUGINS` list. Each plugin must implement `get_metadata()`, `check_applicability(profile)`, `generate_configuration(profile)`, `validate(data, configuration)`, and `explain_failure(result)`. Rule IDs must be unique. Restart the app to discover a newly installed module. Packaged plugins can alternatively register through the `feuji_dq.rules` Python entry-point group.
