# Copyright 2023 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import os.path
import subprocess
from textwrap import dedent

import pytest

from pex.dist_metadata import Distribution
from pex.pep_440 import Version
from pex.pep_503 import ProjectName
from pex.pex import PEX
from pex.testing import make_env, run_pex_command
from pex.typing import TYPE_CHECKING
from pex.venv.virtualenv import Virtualenv

if TYPE_CHECKING:
    from typing import Any, Iterable, Mapping


@pytest.fixture(scope="module")
def td(tmpdir_factory):
    # type: (Any) -> Any
    return tmpdir_factory.mktemp("td")


PIP_PROJECT_NAME = ProjectName("pip")
SETUPTOOLS_PROJECT_NAME = ProjectName("setuptools")


def index_distributions(dists):
    # type: (Iterable[Distribution]) -> Mapping[ProjectName, Version]
    return {dist.metadata.project_name: dist.metadata.version for dist in dists}


@pytest.fixture(scope="module")
def baseline_venv_with_pip(td):
    # type: (Any) -> Mapping[ProjectName, Version]
    baseline_venv = Virtualenv.create(venv_dir=str(td.join("baseline.venv")))
    baseline_venv.install_pip()
    baseline_venv_distributions = index_distributions(baseline_venv.iter_distributions())
    assert {PIP_PROJECT_NAME, SETUPTOOLS_PROJECT_NAME} == set(baseline_venv_distributions)
    return baseline_venv_distributions


@pytest.fixture(scope="module")
def baseline_venv_pip_version(baseline_venv_with_pip):
    # type: (Mapping[ProjectName, Version]) -> Version
    return baseline_venv_with_pip[PIP_PROJECT_NAME]


@pytest.fixture(scope="module")
def baseline_venv_setuptools_version(baseline_venv_with_pip):
    # type: (Mapping[ProjectName, Version]) -> Version
    return baseline_venv_with_pip[SETUPTOOLS_PROJECT_NAME]


def assert_venv_dists(
    venv_dir,  # type: str
    expected_pip_version,  # type: Version
    expected_setuptools_version,  # type: Version
):
    virtualenv = Virtualenv(venv_dir)
    dists = index_distributions(virtualenv.iter_distributions())
    assert expected_pip_version == dists[PIP_PROJECT_NAME]
    assert expected_setuptools_version == dists[SETUPTOOLS_PROJECT_NAME]

    def reported_version(module):
        # type: (str) -> Version
        return Version(
            subprocess.check_output(
                args=[
                    virtualenv.interpreter.binary,
                    "-c",
                    "import {module}; print({module}.__version__)".format(module=module),
                ]
            ).decode("utf-8")
        )

    assert expected_pip_version == reported_version("pip")
    assert expected_setuptools_version == reported_version("setuptools")


def assert_venv_dists_no_conflicts(
    tmpdir,  # type: Any
    pex,  # type: str
    expected_pip_version,  # type: Version
    expected_setuptools_version,  # type: Version
):
    # type: (...) -> None
    venv_dir = os.path.join(str(tmpdir), "venv_dir")
    subprocess.check_call(args=[pex, "venv", "--pip", venv_dir], env=make_env(PEX_TOOLS=1))
    assert_venv_dists(venv_dir, expected_pip_version, expected_setuptools_version)


def test_pip_empty_pex(
    tmpdir,  # type: Any
    baseline_venv_pip_version,  # type: Version
    baseline_venv_setuptools_version,  # type: Version
):
    # type: (...) -> None

    pex = os.path.join(str(tmpdir), "pex")
    run_pex_command(args=["-o", pex, "--include-tools"]).assert_success()

    assert_venv_dists_no_conflicts(
        tmpdir,
        pex,
        expected_pip_version=baseline_venv_pip_version,
        expected_setuptools_version=baseline_venv_setuptools_version,
    )


def test_pip_pex_no_conflicts(
    tmpdir,  # type: Any
    baseline_venv_pip_version,  # type: Version
    baseline_venv_setuptools_version,  # type: Version
):
    # type: (...) -> None

    pex = os.path.join(str(tmpdir), "pex")
    run_pex_command(
        args=[
            "-o",
            pex,
            "pip=={version}".format(version=baseline_venv_pip_version),
            "setuptools=={version}".format(version=baseline_venv_setuptools_version),
            "--include-tools",
        ]
    ).assert_success()

    assert_venv_dists_no_conflicts(
        tmpdir,
        pex,
        expected_pip_version=baseline_venv_pip_version,
        expected_setuptools_version=baseline_venv_setuptools_version,
    )


def assert_venv_dists_conflicts(
    tmpdir,  # type: Any
    pex,  # type: str
    baseline_venv_pip_version,  # type: Version
    baseline_venv_setuptools_version,  # type: Version
    expected_pip_version,  # type: Version
    expected_setuptools_version,  # type: Version
):
    # type: (...) -> None

    expected_conflicts = []
    if baseline_venv_pip_version != expected_pip_version:
        expected_conflicts.append("pip {version}".format(version=expected_pip_version))
    if baseline_venv_setuptools_version != expected_setuptools_version:
        expected_conflicts.append(
            "setuptools {version}".format(version=expected_setuptools_version)
        )
    assert (
        expected_conflicts
    ), "The assert_venv_dists_conflicts function requires at least one conflict."

    venv_dir = os.path.join(str(tmpdir), "venv_dir")
    args = [pex, "venv", "--pip", venv_dir]

    expected_message_prefix = (
        dedent(
            """\
            You asked for --pip to be installed in the venv at {venv_dir},
            but the PEX at {pex} already contains:
            {conflicts}
            """
        )
        .format(venv_dir=venv_dir, pex=pex, conflicts=os.linesep.join(expected_conflicts))
        .strip()
    )

    process = subprocess.Popen(args, stderr=subprocess.PIPE, env=make_env(PEX_TOOLS=1))
    _, stderr = process.communicate()
    assert 0 != process.returncode

    decoded_stderr = stderr.decode("utf-8")
    assert (
        dedent(
            """\
            {prefix}
            Consider re-running either without --pip or with --collisions-ok.
            """
        ).format(prefix=expected_message_prefix)
        in decoded_stderr
    ), decoded_stderr

    process = subprocess.Popen(
        args + ["--force", "--collisions-ok"], stderr=subprocess.PIPE, env=make_env(PEX_TOOLS=1)
    )
    _, stderr = process.communicate()
    assert 0 == process.returncode
    decoded_stderr = stderr.decode("utf-8")
    assert (
        dedent(
            """\
            {prefix}
            Uninstalling venv versions and using versions from the PEX.
            """
        ).format(prefix=expected_message_prefix)
        in decoded_stderr
    ), decoded_stderr

    assert_venv_dists(venv_dir, expected_pip_version, expected_setuptools_version)


def test_pip_pex_pip_conflict(
    tmpdir,  # type: Any
    baseline_venv_pip_version,  # type: Version
    baseline_venv_setuptools_version,  # type: Version
):
    # type: (...) -> None

    pex = os.path.join(str(tmpdir), "pex")
    run_pex_command(
        args=[
            "-o",
            pex,
            "pip!={version}".format(version=baseline_venv_pip_version),
            "--include-tools",
        ]
    ).assert_success()
    pex_pip_version = index_distributions(PEX(pex).resolve())[PIP_PROJECT_NAME]

    assert_venv_dists_conflicts(
        tmpdir,
        pex,
        baseline_venv_pip_version=baseline_venv_pip_version,
        baseline_venv_setuptools_version=baseline_venv_setuptools_version,
        expected_pip_version=pex_pip_version,
        expected_setuptools_version=baseline_venv_setuptools_version,
    )


def test_pip_pex_setuptools_conflict(
    tmpdir,  # type: Any
    baseline_venv_pip_version,  # type: Version
    baseline_venv_setuptools_version,  # type: Version
):
    # type: (...) -> None

    pex = os.path.join(str(tmpdir), "pex")
    run_pex_command(
        args=[
            "-o",
            pex,
            "setuptools!={version}".format(version=baseline_venv_setuptools_version),
            "--include-tools",
        ]
    ).assert_success()
    pex_setuptools_version = index_distributions(PEX(pex).resolve())[SETUPTOOLS_PROJECT_NAME]

    assert_venv_dists_conflicts(
        tmpdir,
        pex,
        baseline_venv_pip_version=baseline_venv_pip_version,
        baseline_venv_setuptools_version=baseline_venv_setuptools_version,
        expected_pip_version=baseline_venv_pip_version,
        expected_setuptools_version=pex_setuptools_version,
    )


def test_pip_pex_both_conflict(
    tmpdir,  # type: Any
    baseline_venv_pip_version,  # type: Version
    baseline_venv_setuptools_version,  # type: Version
):
    # type: (...) -> None

    pex = os.path.join(str(tmpdir), "pex")
    run_pex_command(
        args=[
            "-o",
            pex,
            "pip!={version}".format(version=baseline_venv_pip_version),
            "setuptools!={version}".format(version=baseline_venv_setuptools_version),
            "--include-tools",
        ]
    ).assert_success()
    pex_pip_version = index_distributions(PEX(pex).resolve())[PIP_PROJECT_NAME]
    pex_setuptools_version = index_distributions(PEX(pex).resolve())[SETUPTOOLS_PROJECT_NAME]

    assert_venv_dists_conflicts(
        tmpdir,
        pex,
        baseline_venv_pip_version=baseline_venv_pip_version,
        baseline_venv_setuptools_version=baseline_venv_setuptools_version,
        expected_pip_version=pex_pip_version,
        expected_setuptools_version=pex_setuptools_version,
    )
