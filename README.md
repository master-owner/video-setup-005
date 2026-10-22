# Runner selection fix - 2026.10.20

Copy ALL .github/ and scripts/ contents into the existing repository and commit/push. The bundled .github/actions folder must be committed too. No portal redeploy or new secrets.

Fix: the portal's overlay_layout.runnerMode selection now takes precedence over the GitHub workflow input's injected default single. Manual workflow runner_mode still works when the layout contains no runner selection. Invalid modes fail rather than silently encode on one runner. The pipeline logs the effective runner mode near startup.

This bundle includes concurrent source artifact uploads, download progress and earlier upload-wait changes. Only new runs whose source_commit includes these files use the fix. Restarting an old pinned run retains its original code. No actual workflow was triggered.

Validation: five new mode-precedence/invalid-value/workflow regression tests passed; workflow YAML parsed. Earlier cumulative validation: 100 Python tests and 3 concurrent artifact tests passed before this isolated selection fix.

Important: selecting parallel does not make the initial source download use multiple encoding runners. Download/prepare happen first, followed by up to four chunk workers. H.264 transfer mode can copy the video without encoding and does not need chunk workers.
