from app import reference_data


def test_title_case_name_all_caps():
    assert reference_data.title_case_name("SMITH") == "Smith"


def test_title_case_name_preserves_apostrophe_break():
    assert reference_data.title_case_name("O'BRIEN") == "O'Brien"


def test_title_case_name_preserves_hyphen_break():
    assert reference_data.title_case_name("SMITH-JONES") == "Smith-Jones"


def test_title_case_name_does_not_break_on_accented_letters():
    assert reference_data.title_case_name("PEÑA") == "Peña"
    assert reference_data.title_case_name("MUÑOZ") == "Muñoz"
    assert reference_data.title_case_name("JOÃO") == "João"


def test_title_case_name_empty_or_none():
    assert reference_data.title_case_name(None) == ""
    assert reference_data.title_case_name("") == ""
