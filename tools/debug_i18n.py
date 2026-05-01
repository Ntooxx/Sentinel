import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '../src')

tempdir = tempfile.TemporaryDirectory()
project_root = Path(tempdir.name)

(locales_dir := project_root / "packages" / "app" / "locales").mkdir(parents=True)
(locale_file := locales_dir / "de.json").write_text('{"key": "value"}', encoding='utf-8')

from classify import classifyFile, classifySurface  # noqa: E402
from auditor import ProjectAuditor  # noqa: E402

fc = classifyFile('packages/app/locales/de.json')
print('File classification:')
print(f'  role: {fc.role}')
print(f'  isLocalization: {fc.isLocalization}')
print(f'  isConfig: {fc.isConfig}')
print(f'  isLockfile: {fc.isLockfile}')
print(f'  surface: {classifySurface("packages/app/locales/de.json")}')

audit = ProjectAuditor(str(project_root), str(project_root / 'checkpoints.json'))
files = audit.scan_directory(
    ignore_dirs=['__pycache__'],
    extensions=['.py', '.md', '.txt', '.json'],
    max_size=1024 * 1024,
)

fc2 = classifyFile('packages/app/locales/de.json')
print('\nAfter scan_directory:')
print(f'  role: {fc2.role}')
print(f'  isLocalization: {fc2.isLocalization}')

context = audit._classify_path_context('packages/app/locales/de.json', files.get('packages/app/locales/de.json'))
print(f'\n_audit_path_context result: {context}')

tempdir.cleanup()