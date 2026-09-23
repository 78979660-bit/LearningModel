import random
from study_app.data.text_integrity import corruption_reason, MOJIBAKE_MARKERS, QUESTION_MARK_RUN


def original_reason(text):
    if '\ufffd' in text:
        return '包含 Unicode 替换字符 U+FFFD'
    if any('\ue000' <= char <= '\uf8ff' for char in text):
        return '包含 Unicode 私用区字符，疑似错误解码乱码'
    if QUESTION_MARK_RUN.search(text):
        return '包含连续三个以上问号，疑似字符已被替换'
    markers = sum(text.count(m) for m in MOJIBAKE_MARKERS)
    chinese = sum('\u4e00' <= char <= '\u9fff' for char in text)
    if len(text) >= 6 and markers >= 3 and markers / max(chinese, 1) >= .18:
        return '疑似 UTF-8/GBK 错误解码乱码'
    return None


def test_optimized_scanner_preserves_detection_and_reason_precedence():
    rng = random.Random(42)
    alphabet = 'abc0123?中文你好测试锛銆绋\ue000\uf8ff\ufffd\t\n'
    samples = ['', 'ascii' * 20000, '正常中文' * 1000, '锛' * 3 + '中' * 13, '锛' * 3 + '中' * 14]
    samples += [''.join(rng.choices(alphabet, k=rng.randrange(150))) for _ in range(1000)]
    for text in samples:
        assert corruption_reason(text) == original_reason(text)
