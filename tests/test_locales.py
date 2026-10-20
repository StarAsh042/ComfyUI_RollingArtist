"""文案一致性测试：三处界面文案来源必须对齐。

背景：界面文案有**三处**来源——

1. 代码里的默认 tooltip（``INPUT_TYPES`` 的 ``tooltip`` 键）
2. ``locales/zh/nodeDefs.json``
3. ``locales/en/nodeDefs.json``

三份内容没有自动同步机制，容易漂移（例如新增参数只加了中文、忘了英文）。
这里把"语言包之间必须对齐"和"语言包必须覆盖所有输入参数"钉成测试，
让漂移在 CI 阶段就暴露，而不是等用户切换语言时才发现。

**代码默认文案 == zh 语言包**：代码里的默认 tooltip 是中文，供前端在语言包
缺失/未加载时兜底，语义上就是「中文文案的权威副本」。因此这里要求二者
**逐字相同**——本测试首次启用时立刻抓到两处已发生的漂移（``weight_curve``
多了一句开头、``copyright_pick`` 少了「默认数据约 3460 个」），证明这条校验
不是形式主义，而是真能拦住已经发生过的分叉。
"""

import json
import os

import pytest

NODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALES_DIR = os.path.join(NODE_DIR, "locales")

LANGUAGES = ("en", "zh")


def _load(language, filename):
    path = os.path.join(LOCALES_DIR, language, filename)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _code_inputs(node_cls):
    """取节点 ``INPUT_TYPES()`` 里的全部输入（required + optional 合并）。

    返回 ``{输入名: 参数规格 dict}``；非 ``(类型, 规格)`` 形式的项（理论上不存在）
    会被跳过，避免因结构意外直接抛异常。
    """
    spec = node_cls.INPUT_TYPES()
    merged = {}
    for group in ("required", "optional"):
        for name, definition in spec.get(group, {}).items():
            if isinstance(definition, tuple) and len(definition) >= 2 and isinstance(definition[1], dict):
                merged[name] = definition[1]
    return merged


def _node_classes():
    """两个节点的类对象（延迟导入：测试收集阶段不依赖 ComfyUI 已加载）。"""
    import ComfyUI_RollingArtist.RollingArtist as artist_mod
    import ComfyUI_RollingArtist.RollingCharacter as character_mod

    return artist_mod.RollingArtist, character_mod.RollingCharacter


def _collect_keys(node_defs):
    """收集某语言包里所有 (节点, 输入名) 键与节点名集合。"""
    inputs = set()
    display = set()
    for node_name, node in node_defs.items():
        display.add(node_name)
        for input_name in node.get("inputs", {}):
            inputs.add((node_name, input_name))
    return display, inputs


@pytest.mark.parametrize("language", LANGUAGES)
def test_locale_files_exist(language):
    assert _load(language, "nodeDefs.json") is not None, f"{language}/nodeDefs.json 缺失"
    assert _load(language, "main.json") is not None, f"{language}/main.json 缺失"


def test_node_key_sets_match_across_languages():
    """各语言包覆盖的节点集合必须完全相同。"""
    en = _load("en", "nodeDefs.json")
    zh = _load("zh", "nodeDefs.json")
    assert en is not None and zh is not None

    en_nodes, en_inputs = _collect_keys(en)
    zh_nodes, zh_inputs = _collect_keys(zh)

    assert en_nodes == zh_nodes, (
        f"节点集合不一致：仅英文有 {en_nodes - zh_nodes}，仅中文有 {zh_nodes - en_nodes}"
    )
    assert en_inputs == zh_inputs, (
        f"输入参数集合不一致：仅英文有 {en_inputs - zh_inputs}，"
        f"仅中文有 {zh_inputs - en_inputs}"
    )


def test_locales_cover_all_node_inputs():
    """语言包必须覆盖节点代码里定义的全部输入参数。

    漏翻的参数在英文界面下会显示为空，属于明显的体验缺陷。
    """
    for language in LANGUAGES:
        node_defs = _load(language, "nodeDefs.json")
        assert node_defs is not None

        for node_cls in _node_classes():
            node_name = node_cls.__name__
            code_names = set(_code_inputs(node_cls))
            locale_names = set(node_defs.get(node_name, {}).get("inputs", {}))
            missing = code_names - locale_names
            assert not missing, (
                f"{language} 语言包缺少 {node_name} 的输入文案：{sorted(missing)}"
            )


@pytest.mark.parametrize("node_name", ["RollingArtist", "RollingCharacter"])
def test_code_default_tooltips_match_zh_locale(node_name):
    """代码里的默认 tooltip 必须与 zh 语言包**逐字一致**。

    代码默认文案就是中文兜底副本，与 ``locales/zh/nodeDefs.json`` 说的是同一件事。
    两份分开维护时极易分叉——本测试启用时即抓到 ``weight_curve`` 与
    ``copyright_pick`` 两处已经漂移的文案，说明这是真实存在的维护风险。

    校验范围同时覆盖 ``name``（显示名）：显示名同样有「代码 default / zh」两份。
    """
    zh = _load("zh", "nodeDefs.json")
    assert zh is not None

    node_cls = next(cls for cls in _node_classes() if cls.__name__ == node_name)
    code_inputs = _code_inputs(node_cls)
    zh_inputs = zh.get(node_name, {}).get("inputs", {})

    mismatches = []
    for input_name, spec in code_inputs.items():
        code_tip = str(spec.get("tooltip", "")).strip()
        zh_tip = str(zh_inputs.get(input_name, {}).get("tooltip", "")).strip()
        if code_tip != zh_tip:
            mismatches.append(
                f"  {input_name}:\n"
                f"    代码: {code_tip!r}\n"
                f"    zh  : {zh_tip!r}"
            )
    assert not mismatches, (
        f"{node_name} 的代码默认文案与 zh 语言包不一致（共 {len(mismatches)} 处）：\n"
        + "\n".join(mismatches)
    )


def test_code_default_tooltips_are_non_empty():
    """代码里的默认 tooltip 不得为空（空 tooltip 会让前端显示一片空白）。"""
    for node_cls in _node_classes():
        for input_name, spec in _code_inputs(node_cls).items():
            assert str(spec.get("tooltip", "")).strip(), (
                f"{node_cls.__name__}.{input_name} 缺少代码默认 tooltip"
            )


def test_every_locale_tooltip_is_non_empty():
    """语言包里的 name / tooltip 不得为空字符串（空值等于没翻）。"""
    for language in LANGUAGES:
        node_defs = _load(language, "nodeDefs.json")
        assert node_defs is not None
        for node_name, node in node_defs.items():
            for input_name, spec in node.get("inputs", {}).items():
                assert spec.get("name", "").strip(), (
                    f"{language}/{node_name}.{input_name} 的 name 为空"
                )
                assert spec.get("tooltip", "").strip(), (
                    f"{language}/{node_name}.{input_name} 的 tooltip 为空"
                )
