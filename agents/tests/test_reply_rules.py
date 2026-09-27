from app.loops import reply_problems


def test_hindi_message_needs_devanagari_reply():
    assert reply_problems("अजय मेरा भतीजा है।", "Vikram ji, Ajay aapke bhatije hain.")
    assert not reply_problems("अजय मेरा भतीजा है।", "विक्रम जी, अजय आपके भतीजे हैं।")


def test_english_message_needs_latin_reply():
    assert reply_problems("Can you use Ajay as my nominee?", "विक्रम जी, ठीक है।")
    assert not reply_problems("Can you use Ajay as my nominee?", "Of course, Vikram ji.")


def test_false_saved_claims_are_caught():
    assert reply_problems("Ajay is my nephew", "I have noted that Ajay is your nephew.")
    assert reply_problems("Ajay mera bhatija hai", "Maine note kar liya hai.")
    assert reply_problems("अजय मेरा भतीजा है", "मैंने नोट कर लिया है।")
    assert not reply_problems("Ajay is my nephew", "I've added that to your list to review, Vikram ji.")


def test_asking_to_add_is_caught():
    assert reply_problems("My first job was in Mysore", "Would you like me to add this story to your list?")
    assert reply_problems("I have an LIC policy", "Shall I save this for you?")
    assert not reply_problems("I have an LIC policy", "Who is the nominee on that policy, Vikram ji?")
