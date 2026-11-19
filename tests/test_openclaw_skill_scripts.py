"""The skill's helper scripts locate the repository from any working directory."""
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPTS = Path(__file__).parents[1] / 'openclaw' / 'skills' / 'scripts'
STUB = "import os, sys\nprint('{name}', *sys.argv[1:], 'cwd=' + os.path.realpath(os.getcwd()))\n"

pytestmark = pytest.mark.skipif(not shutil.which('bash') or not shutil.which('python3'),
                                reason='needs bash and python3')


def run(script, *args, cwd):
    return subprocess.run(['bash', str(script), *args], cwd=cwd, capture_output=True, text=True, timeout=60)


@pytest.fixture
def repository(tmp_path):
    """A synthetic checkout: the real helper scripts beside stand-in engine scripts."""
    root = tmp_path / 'checkout'
    shutil.copytree(SCRIPTS, root / 'openclaw' / 'skills' / 'scripts')
    engine = root / 'engine' / 'scripts'
    engine.mkdir(parents=True)
    for name in ('portfolio_manager', 'portfolio_snapshot', 'portfolio_report'):
        (engine / (name + '.py')).write_text(STUB.format(name=name))
    (tmp_path / 'elsewhere').mkdir()
    return root


def test_shipped_manage_script_finds_this_repository_from_another_directory(tmp_path):
    result = run(SCRIPTS / 'manage.sh', cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Quick Commands' in result.stdout and 'Cannot find project root' not in result.stdout


@pytest.mark.parametrize('start', ['elsewhere', 'checkout', 'checkout/openclaw', 'checkout/openclaw/skills/scripts'])
def test_manage_runs_the_manager_from_any_directory(repository, tmp_path, start):
    script = repository / 'openclaw' / 'skills' / 'scripts' / 'manage.sh'
    result = run(script, 'show', '--group', 'Example', cwd=tmp_path / start)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith('portfolio_manager show --group Example')
    relative = run('manage.sh', 'groups', cwd=script.parent)
    assert relative.returncode == 0 and relative.stdout.startswith('portfolio_manager groups')


@pytest.mark.parametrize('start', ['elsewhere', 'checkout', 'checkout/engine'])
def test_daily_pipeline_runs_in_the_engine_directory_from_any_directory(repository, tmp_path, start):
    script = repository / 'openclaw' / 'skills' / 'scripts' / 'daily-pipeline.sh'
    result = run(script, '2026-01-02', cwd=tmp_path / start)
    assert result.returncode == 0, result.stdout + result.stderr
    engine = 'cwd=' + str((repository / 'engine').resolve())
    assert f'portfolio_snapshot --date 2026-01-02 {engine}' in result.stdout
    assert f'portfolio_report --date 2026-01-02 {engine}' in result.stdout
    assert 'Push skipped' in result.stdout


def test_scripts_follow_a_linked_skill_directory(repository, tmp_path):
    link = tmp_path / 'installed-skill'
    link.symlink_to(repository / 'openclaw' / 'skills', target_is_directory=True)
    result = run(link / 'scripts' / 'manage.sh', 'show', cwd=tmp_path / 'elsewhere')
    assert result.returncode == 0 and result.stdout.startswith('portfolio_manager show')


def test_copied_scripts_refuse_even_inside_another_checkout(repository, tmp_path):
    copied = tmp_path / 'copied' / 'scripts'
    shutil.copytree(SCRIPTS, copied)
    for name in ('manage.sh', 'daily-pipeline.sh'):
        refused = run(copied / name, 'show', cwd=tmp_path / 'elsewhere')
        assert refused.returncode == 1 and 'Cannot find project root' in refused.stdout
        assert 'portfolio_' not in refused.stdout
    # A copy never borrows the checkout it happens to be run from.
    for name in ('manage.sh', 'daily-pipeline.sh'):
        borrowed = run(copied / name, 'show', cwd=repository)
        assert borrowed.returncode == 1 and 'Cannot find project root' in borrowed.stdout
        assert 'portfolio_' not in borrowed.stdout


@pytest.mark.parametrize('name', ['manage.sh', 'daily-pipeline.sh'])
def test_a_symlink_to_the_script_uses_the_scripts_own_checkout(repository, tmp_path, name):
    link = tmp_path / 'bin' / name
    link.parent.mkdir()
    link.symlink_to(repository / 'openclaw' / 'skills' / 'scripts' / name)
    other = tmp_path / 'other'
    (other / 'engine' / 'scripts').mkdir(parents=True)
    (other / 'engine' / 'scripts' / 'portfolio_manager.py').write_text(STUB.format(name='WRONG'))
    (other / 'engine' / 'scripts' / 'portfolio_snapshot.py').write_text(STUB.format(name='WRONG'))
    for start in (tmp_path / 'elsewhere', other):
        result = run(link, '2026-01-02', cwd=start)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'portfolio_' in result.stdout and 'WRONG' not in result.stdout and 'Cannot find project root' not in result.stdout
        if name == 'daily-pipeline.sh':
            assert 'cwd=' + str((repository / 'engine').resolve()) in result.stdout


def test_a_chain_of_relative_symlinks_is_followed(repository, tmp_path):
    scripts = repository / 'openclaw' / 'skills' / 'scripts'
    (tmp_path / 'a').mkdir()
    (tmp_path / 'b').mkdir()
    (tmp_path / 'b' / 'step').symlink_to(scripts / 'manage.sh')
    (tmp_path / 'a' / 'manage.sh').symlink_to('../b/step')
    result = run(tmp_path / 'a' / 'manage.sh', 'groups', cwd=tmp_path / 'elsewhere')
    assert result.returncode == 0 and result.stdout.startswith('portfolio_manager groups')
