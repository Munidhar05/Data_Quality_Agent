"""Verify the real development entrypoint does not watch installed packages."""
import ast
from pathlib import Path

import uvicorn
from uvicorn import Config
from uvicorn.supervisors.watchfilesreload import FileFilter


def test_development_watcher_ignores_dependencies_and_generated_files(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    module = ast.parse((root / 'backend' / 'app.py').read_text(encoding='utf-8'))
    startup = module.body[-1]
    captured = {}
    def capture(application, **options):
        captured.update(application=application, **options)
    monkeypatch.setattr(uvicorn, 'run', capture)
    exec(compile(ast.Module(body=[startup], type_ignores=[]), 'startup', 'exec'),
         {'__name__': '__main__', 'APP_ROOT': root})
    application = captured.pop('application')
    config = Config(application, **captured)
    assert config.reload_dirs == [root / 'backend']
    accepts = FileFilter(config)
    assert accepts(root / 'backend' / 'app.py')
    assert accepts(root / 'backend' / 'rule_registry.json')
    assert not accepts(root / '.venv' / 'Lib' / 'site-packages' / 'pandas' / '__init__.py')
    assert not accepts(root / 'datasets' / 'uploads' / 'generated.json')
