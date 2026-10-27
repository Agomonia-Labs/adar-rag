from services.language import infer_language_from_text


def test_detects_bangla_script():
    assert infer_language_from_text("এই ভিডিওতে কী আলোচনা করা হয়েছে?") == "bn"


def test_detects_hindi_script():
    assert infer_language_from_text("इस वीडियो में क्या बताया गया है?") == "hi"


def test_detects_arabic_script():
    assert infer_language_from_text("ماذا يناقش هذا الفيديو؟") == "ar"


def test_latin_script_requires_explicit_language():
    assert infer_language_from_text("What is discussed in this video?") is None
