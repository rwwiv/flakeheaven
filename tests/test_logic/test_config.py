# external
import pytest

# project
from flakeheaven._logic import _config


def test_deep_update_deep_copy():
    new_dict = _config._deep_update({'subdict': {'key_1': 'value_1'}},
                                    {'subdict': {'key_2': 'value_2'}})

    assert new_dict == {'subdict': {'key_1': 'value_1', 'key_2': 'value_2'}}


def test_deep_update_shallow_copy():
    new_dict = _config._deep_update({'subdict': {'key_1': 'value_1'}},
                                    {'parent_key': 'parent_value'})

    assert new_dict == {'subdict': {'key_1': 'value_1'}, 'parent_key': 'parent_value'}


def test_merge_config_merges_root_keys():
    merged_config = _config._merge_configs({'key_1': 'value_1'}, {'key_2': 'value_2'})

    assert merged_config == {'key_1': 'value_1', 'key_2': 'value_2'}


@pytest.mark.parametrize('subdict', ['plugins', 'exceptions'])
def test_merge_config_merges_subdicts(subdict):
    merged_config = _config._merge_configs({subdict: {'key_1': 'value_1'}}, {subdict: {'key_2': 'value_2'}})

    assert merged_config == {subdict: {'key_1': 'value_1', 'key_2': 'value_2'}}


@pytest.mark.parametrize('subdict', ['plugins', 'exceptions'])
def test_merge_config_overwrites_default(subdict):
    merged_config = _config._merge_configs({subdict: {'key_1': 'value_1'}}, {subdict: {'key_1': 'new_value_1'}})

    assert merged_config == {subdict: {'key_1': 'new_value_1'}}


def test_merge_config_empty_dicts():
    merged_config = _config._merge_configs({}, {})

    assert merged_config == {}
