from __future__ import annotations

from douyin_rpa.selectors import fill_editor, find_visible, has_visible_text


def test_find_visible_skips_disabled_and_hidden(browser_context):
    page = browser_context.new_page()
    page.set_content(
        """
        <button disabled>发布</button>
        <button id="enabled">发布</button>
        <div style="display:none">扫码登录</div>
        """
    )

    button = find_visible(page, page, ["role:button:发布"], require_enabled=True)
    assert button is not None
    assert button.get_attribute("id") == "enabled"

    plain = find_visible(page, page, ["role:button:发布"], require_enabled=False)
    assert plain is not None
    assert not has_visible_text(page, ["扫码登录"])


def test_placeholder_and_editor(browser_context):
    page = browser_context.new_page()
    page.set_content(
        """
        <input placeholder="填写作品标题">
        <div class="zone-container">
          <div contenteditable="true" data-placeholder="添加作品描述"></div>
        </div>
        """
    )

    title = find_visible(page, page, ["placeholder:作品标题"])
    assert title is not None
    title.fill("标题")

    editor = find_visible(page, page, ["css:.zone-container [contenteditable='true']"])
    assert editor is not None
    fill_editor(page, editor, "中文文案 #日常")

    assert "中文文案" in editor.inner_text()
    assert "#日常" in editor.inner_text()


def test_exact_text_vs_substring(browser_context):
    page = browser_context.new_page()
    page.set_content("<div>作品发布及管理</div><div>发布</div>")

    exact = find_visible(page, page, ["text!:发布"])
    assert exact is not None
    assert exact.inner_text() == "发布"
