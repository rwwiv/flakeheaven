# built-in
import sys
from argparse import ArgumentParser, Namespace
from itertools import chain
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# external
from flake8.main.application import Application
from flake8.options.config import ConfigParser, get_local_plugins
from flake8.options.manager import Option
from flake8.plugins.manager import ReportFormatters
from flake8.utils import parse_unified_diff

# app
from .._constants import DEFAULTS
from .._logic import read_config
from ._checkers import FlakeHeavenCheckersManager
from ._plugins import FlakeHeavenCheckers
from ._style_guide import FlakeHeavenStyleGuideManager


# Options that are related to the flake8 codes management logic.
# We use our own codes management via `plugins` and `exceptions`.
UNSUPPORTED = frozenset({
    '--extend-exclude',     # use only `exclude` in the config
    '--per-file-ignores',   # use `exceptions`
    '--statistics',         # use `--format=stat` instead

    '--ignore',             # use `plugins`
    '--extend-ignore',      # use `plugins`
    '--select',             # use `plugins`
    '--enable-extensions',  # use `plugins`
})


class FlakeHeavenApplication(Application):
    """
    Reloaded flake8 original entrypoint to provide support for some features:
    + pyproject.toml support
    + replace CheckersManager to support for `plugins` option
    + register custom formatters
    """
    guide: FlakeHeavenStyleGuideManager

    @property
    def option_manager(self):
        """We overload this property only to specify setter.
        """
        return self._option_manager

    @option_manager.setter
    def option_manager(self, manager):
        """Hook to add flakeheaven options into flake8 options parser.
        """
        group = manager.parser.add_argument_group('FlakeHeaven')
        group.add_argument('--baseline', help='path to baseline')
        group.add_argument('--relative', action='store_true',
                           help='Treat file paths as relative to directory containing baseline file')
        group.add_argument('--safe', action='store_true', help='suppress exceptions from plugins')
        self._option_manager = manager

    def get_toml_config(
        self,
        path: Optional[Path] = None,
        *,
        enforce_keys_from: Dict[str, Option],
    ) -> Dict[str, Any]:
        """Extract config from TOML.

        Args:
            path: toml filepath. If not set, searches in cwd parents.
            enforce_keys_from: Mapping of configuration option names to
             :class:`~flake8.options.manager.Option` instances. It is
             used to convert ``dashed-names`` in `toml` to
             :class:`~flake8.options.config.ConfigParser` namespace so
             it can be updated via its ``__dict__``. Typically, it comes
             from either
             :attr:`~flake8.options.config.ConfigParser.config_options`,
             or directly from
             :attr:`~flake8.options.manager.OptionManager.config_options_dict`.
        """
        if path is not None:
            toml_config = read_config(path)
        else:
            # lookup for config from current dir up to root
            root = Path().resolve()
            for dir_path in chain([root], root.parents):
                path = dir_path / 'pyproject.toml'
                if path.exists():
                    toml_config = read_config(path)
                    break
            else:
                toml_config = {}

        for name in list(toml_config.keys()):
            try:
                option = enforce_keys_from[name]
                dst = option.config_name
                if dst == name:
                    continue
                if dst is None:
                    raise ValueError(
                        f'Unable to parse `{path}`. '
                        f'Reason: option {option}.config_name not set. '
                        f'Maybe its not enabled as `parse_from_config`?'  # noqa: C812
                    )
            except KeyError:
                continue

            toml_config[dst] = toml_config.pop(name)
        return toml_config

    @staticmethod
    def extract_toml_config_path(argv: List[str]) -> Tuple[Optional[Path], List[str]]:
        if not argv:
            return None, argv

        if '--help' in argv:
            argv = argv.copy()
            argv.remove('--help')
            if not argv:
                return None, ['--help']

        parser = ArgumentParser()
        parser.add_argument('--config')
        known, unknown = parser.parse_known_args(argv)
        if known.config and known.config.endswith('.toml'):
            return Path(known.config).expanduser(), unknown
        return None, argv

    def parse_preliminary_options(
        self, argv: List[str],
    ) -> Tuple[Namespace, List[str]]:
        # if passed `--config` with path to TOML-config, we should extract it
        # before passing into flake8 mechanisms
        self._config_path, argv = self.extract_toml_config_path(argv=argv)
        return super().parse_preliminary_options(argv)

    def parse_configuration_and_cli(self, config_finder, argv: List[str]) -> None:
        parser = self.option_manager.parser
        for action in parser._actions.copy():
            if not action.option_strings:
                continue
            name = action.option_strings[-1]
            if name not in UNSUPPORTED:
                continue
            parser._handle_conflict_resolve(None, [(name, action)])

        # make default config
        config, _ = self.option_manager.parse_args([])
        config.__dict__.update(DEFAULTS)

        # Parse CLI options and legacy flake8 configs.
        # Based on `aggregate_options`.
        config_parser = ConfigParser(
            option_manager=self.option_manager,
            config_finder=config_finder,
        )
        parsed_config = config_parser.parse()

        toml_config = self.get_toml_config(
            self._config_path,
            enforce_keys_from=config_parser.config_options,
        )

        config.__dict__.update(toml_config)

        config.extended_default_select = self.option_manager.extended_default_select.copy()
        config.extended_default_ignore = self.option_manager.extended_default_ignore.copy()

        for config_name, value in parsed_config.items():
            dest_name = config_name
            # If the config name is somehow different from the destination name,
            # fetch the destination name from our Option
            if not hasattr(config, config_name):
                dest_name = config_parser.config_options[config_name].dest
            setattr(config, dest_name, value)
        self.options, self.args = self.option_manager.parse_args(
            args=argv,
            values=config,
        )

        # All this goes from the original `parse_configuration_and_cli`.
        # We can't call `super` anymore because all `Application` methods
        # redefine everything.
        self.running_against_diff = self.options.diff
        if self.running_against_diff:
            self.parsed_diff = parse_unified_diff()
            if not self.parsed_diff:
                self.exit()
        self.options._running_from_vcs = False
        self.check_plugins.provide_options(
            optmanager=self.option_manager,
            options=self.options,
            extra_args=self.args,
        )
        self.formatting_plugins.provide_options(
            optmanager=self.option_manager,
            options=self.options,
            extra_args=self.args,
        )

    def make_file_checker_manager(self) -> None:
        self.file_checker_manager = FlakeHeavenCheckersManager(
            baseline=self.options.baseline,
            style_guide=self.guide,
            arguments=self.args,
            checker_plugins=self.check_plugins,
            relative=self.options.relative,
        )

    def find_plugins(self, config_finder) -> None:
        local_plugins = get_local_plugins(config_finder)
        sys.path.extend(local_plugins.paths)
        self.check_plugins = FlakeHeavenCheckers(local_plugins.extension)  # this line is changed
        self.formatting_plugins = ReportFormatters(local_plugins.report)
        self.check_plugins.load_plugins()
        self.formatting_plugins.load_plugins()

    def make_formatter(self, *args, **kwargs) -> None:
        if self.formatter is None:
            super().make_formatter(*args, **kwargs)

    def make_guide(self) -> None:
        """Patched StyleGuide creation just to use FlakeHeavenStyleGuideManager
        instead of original one.
        """
        if self.guide is None:
            self.guide = FlakeHeavenStyleGuideManager(self.options, self.formatter)

        if self.running_against_diff:
            self.guide.add_diff_ranges(self.parsed_diff)
