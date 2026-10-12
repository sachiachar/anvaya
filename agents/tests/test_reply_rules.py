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


# ---- Never the same question twice in a conversation (the demo asked about advice three times) -----

ADVICE = "Namaste Vikram ji. Is there any advice you would like to share with Priya and Arjun?"
ACCOUNT = "Could you tell me about one account or policy that your family should know about?"


def test_asking_again_what_saathi_already_asked_is_caught():
    again = "That is a beautiful memory. Would you like to share any advice with Priya and Arjun that comes from your own life?"
    assert reply_problems("My first job was at a textile mill.", again, [ADVICE])
    assert reply_problems("I have an LIC policy.", "Would you like to share any specific advice with Priya and Arjun today?",
                          [ADVICE])
    assert reply_problems("My wish is about the flat.",
                          "Could you share the name of one bank account or insurance policy they should know about?",
                          [ACCOUNT])


def test_a_new_question_about_the_same_people_is_fine():
    assert not reply_problems("I have an LIC policy.", "Does Priya know where the papers for it are kept?", [ADVICE])
    assert not reply_problems("Hello", ADVICE, [])  # the first time is fine


def test_a_repeated_question_is_written_again_once(monkeypatch):
    import asyncio

    from app import loops
    from app.schemas import PlanReply
    replies = iter(["Would you like to share any advice with Priya and Arjun?", "Who is the nominee, Vikram ji?"])
    prompts = []

    async def once(owner, prompt):
        prompts.append(prompt)
        return PlanReply(reply=next(replies), reply_language="en-IN")

    monkeypatch.setattr(loops, "_respond_once", once)
    result = asyncio.run(loops.respond({"name": "Vikram"}, None, None, "I have an LIC policy.", {}, [ADVICE]))
    assert result.reply == "Who is the nominee, Vikram ji?" and len(prompts) == 2
    assert "You already asked this" in prompts[1]
