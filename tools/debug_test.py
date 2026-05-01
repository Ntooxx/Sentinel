import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '../src')

tempdir = tempfile.TemporaryDirectory()
project_root = Path(tempdir.name)

nvidia_file = project_root / 'nvidia_nim_models.json'
nvidia_file.write_text('["model"]', encoding='utf-8')

from classify import classifyFile, classifyLargeFilePolicy  # noqa: E402
from auditor import ProjectAuditor  # noqa: E402

auditor = ProjectAuditor(str(project_root), str(project_root / 'checkpoints.json'))
files = auditor.scan_directory(
    ignore_dirs=['__pycache__'],
    extensions=['.py', '.md', '.txt', '.json'],
    max_size=1024 * 1024,
)

fc = classifyFile('nvidia_nim_models.json')
policy = classifyLargeFilePolicy('nvidia_nim_models.json')
print('File classification:', fc.role, fc.isConfig, fc.isLockfile)
print('Large file policy:', policy)

audit = auditor.audit_project(files)
issues = audit['issues']
for issue in issues:
    if issue.get('file') == 'nvidia_nim_models.json':
        print('Issue category:', issue['category'])

tempdir.cleanup()