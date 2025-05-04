import streamlit as st
from datetime import datetime, date
import random

from google_sheets import (
    get_student_info,
    create_student_if_missing,
    add_goal_history_entry,
    update_student_current_goal
)
from goal_bank_loader import (
    load_goal_bank,
    get_random_warmup,
    get_gpt_prompt,
    get_config_value,
    get_goal_text_list
)
from openai import OpenAI

# --- GPT Client Setup ---
openai_client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])


def summarize_background_response(response_text):
    prompt = (
        "Summarize this student response in one sentence, keeping it concise and concrete. "
        "Focus on what the student enjoys, cares about, or finds meaningful:\n\n"
        f'"{response_text}"\n\nSummary:'
    )
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=50
        )
        return response.choices[0].message.content.strip()
    except Exception:
        st.warning("⚠️ There was an error with the GPT API. We'll still continue.")
        return "shared something about themselves"

# --- Cache the goal bank config in session ---
if "goal_bank_config" not in st.session_state:
    st.session_state.goal_bank_config = load_goal_bank()
cfg = st.session_state.goal_bank_config

# --- Session state setup ---
if "step" not in st.session_state:
    st.session_state.step = "enter_id"

st.set_page_config(page_title="Class Contribution Reflection", layout="centered")
st.title("Class Contribution Reflection")

# --- STEP: Enter Student ID ---
if st.session_state.step == "enter_id":
    student_id = st.text_input("Enter your Student ID:")
    if student_id:
        student = get_student_info(student_id)
        if student:
            st.session_state.student_id = student_id
            st.session_state.student = student
            st.session_state.step = "check_goal"
            st.rerun()
        else:
            st.warning("Student ID not found. Please register below.")
            nickname = st.text_input("Nickname")
            pronoun_code = st.text_input("Pronouns (e.g., she/her)")
            chosen_tone = st.selectbox("Preferred Tone", ["Reflective", "Coach", "Challenger"])
            if st.button("Register Student"):
                created = create_student_if_missing(student_id, nickname, pronoun_code, chosen_tone)
                if created:
                    st.success("Student created. Reloading...")
                    st.rerun()

# --- STEP: Check for existing goal ---
elif st.session_state.step == "check_goal":
    student = st.session_state.student
    goal_date = student["CurrentGoalSetDate"]
    # Always go to warmup first
    st.session_state.goal_status = "recent" if (
        goal_date and (date.today() - datetime.strptime(goal_date, "%Y-%m-%d").date()).days
        <= get_config_value(cfg, "max_days_since_goal", 4)
    ) else "none"

    st.session_state.step = "warmup"
    st.rerun()

# --- STEP: Warm Upt ---
# Update for "warmup" step to include warm-up prompt regeneration and one-word options.
elif st.session_state.step == "warmup":
    student = st.session_state.student
    nickname = student.get("Nickname", "there")
    st.markdown(f"👋 Hey **{nickname}**, we'll start today's reflection with a quick check-in. "
                "This helps provide background so I can better help you reflect.")

    # Initialize warmup prompt and word lists
    if "current_warmup_prompt" not in st.session_state:
        st.session_state.current_warmup_prompt = get_random_warmup(cfg, "humanizing")

    if "selected_words" not in st.session_state:
        st.session_state.selected_words = []

    if "one_word_options" not in st.session_state:
        st.session_state.one_word_options = random.sample(cfg["warmup_prompts"]["one_word"], k=5)

    # Show humanizing prompt
    st.markdown(f"**{st.session_state.current_warmup_prompt}**")
    response = st.text_input("Your response:")

    st.markdown("AND / OR")
    st.markdown("Choose one or more words that fit how you're feeling today:")

    # Show 5 one-word options as buttons
    cols = st.columns(len(st.session_state.one_word_options))
    for i, word in enumerate(st.session_state.one_word_options):
        if cols[i].button(word, key=f"word_{word}"):
            if word not in st.session_state.selected_words:
                st.session_state.selected_words.append(word)

    # Show selected words in light blue
    if st.session_state.selected_words:
        selected_display = ", ".join(
            f"<span style='color:lightblue;font-weight:bold'>{w}</span>" for w in st.session_state.selected_words
        )
        st.markdown(f"**Selected words:** {selected_display}", unsafe_allow_html=True)

    st.markdown("---")
    colA, colB = st.columns([1, 1])

    with colA:
        if st.button("Continue"):
            summary_input = response if response else ", ".join(st.session_state.selected_words)
            summary = summarize_background_response(summary_input)
            existing_info = student.get("BackgroundInfo", "")
            combined_info = f"{existing_info} | {summary}".strip(" |")
            st.session_state.background_info = combined_info

            if st.session_state.goal_status == "recent":
                st.session_state.step = "reflect_on_goal"
            else:
                st.session_state.step = "first_goal"
            st.rerun()

    with colB:
        if st.button("Refresh Options"):
            st.session_state.current_warmup_prompt = get_random_warmup(cfg, "humanizing")
            st.session_state.one_word_options = random.sample(cfg["warmup_prompts"]["one_word"], k=5)
            st.rerun()




# --- STEP: First goal setting using YAML goals ---
elif st.session_state.step == "first_goal":
    st.header("Set your first class contribution goal")
    from PIL import Image
    img = Image.open("assets/doyoutalk.jpg")
    st.image(img, use_container_width=True)
    st.markdown("*Use this flowchart to choose a goal, then select it from the drop down.*")

    goal_options = get_goal_text_list(cfg)
    final_goal = st.selectbox("Choose your goal:", goal_options)

    success = st.text_area("How will you know you're succeeding?")
    if st.button("Save First Goal"):
        update_student_current_goal(
            student_id=st.session_state.student_id,
            new_goal=final_goal,
            new_success_measures=success,
            set_date=str(date.today()),
            background_info=st.session_state.get("background_info", "")
        )
        st.success("Goal saved. You can reflect on it next time.")
        st.session_state.step = "done"

# --- STEP: Reflection on existing goal ---
elif st.session_state.step == "reflect_on_goal":
    student = st.session_state.student
    st.markdown("### Reflect on Your Goal")
    st.markdown(f"**Goal:** {student['CurrentGoal']}")
    st.markdown(f"**Success Measures:** {student['CurrentSuccessMeasures']}")
    st.markdown(f"**Set On:** {student['CurrentGoalSetDate']}")

    attempted = st.radio("Did you try this goal today?", [
        "Yes - it worked",
        "Yes - but it felt awkward",
        "No - I forgot",
        "No - I held back"
    ])
    reflection = st.text_area("What helped or got in the way?")

    if st.button("Submit Reflection"):
        add_goal_history_entry({
            "StudentID": st.session_state.student_id,
            "GoalSetDate": student["CurrentGoalSetDate"],
            "GoalText": student["CurrentGoal"],
            "SuccessMeasure": student["CurrentSuccessMeasures"],
            "OutcomeReflection": reflection,
            "GoalAchievement": attempted,
            "InterpretationSummary": "",  # Optional: score logic later
            "BackgroundInfo": student.get("BackgroundInfo", "")
        })
        st.session_state.step = "new_goal"
        st.rerun()

# --- STEP: Spontaneous reflection ---
elif st.session_state.step == "spontaneous_reflection":
    st.markdown("### Reflect on today’s class")
    contrib = st.radio("Did you contribute today?", [
        "Said something in a group",
        "Asked a partner a question",
        "Listened but didn’t speak",
        "Nothing today"
    ])
    thoughts = st.text_area("Anything else you want to share?")
    if st.button("Submit Reflection"):
        add_goal_history_entry({
            "StudentID": st.session_state.student_id,
            "GoalSetDate": "",
            "GoalText": "",
            "SuccessMeasure": "",
            "OutcomeReflection": thoughts,
            "GoalAchievement": contrib,
            "InterpretationSummary": "No prior goal",
            "BackgroundInfo": st.session_state.student.get("BackgroundInfo", "")
        })
        st.session_state.step = "new_goal"
        st.rerun()

# --- STEP: Set new goal using YAML goals ---
elif st.session_state.step == "new_goal":
    st.header("Set a new goal (optional)")
    from PIL import Image

    img = Image.open("assets/doyoutalk.jpg")
    st.image(img, use_container_width=True)
    st.markdown("*Use this flowchart to choose a goal, then select it from the drop down.*")

    goal_options = get_goal_text_list(cfg)
    final_goal = st.selectbox("Choose your goal:", goal_options)

    measure = st.text_area("How will you know you’re succeeding?")
    if st.button("Set Goal"):
        update_student_current_goal(
            student_id=st.session_state.student_id,
            new_goal=final_goal,
            new_success_measures=measure,
            set_date=str(date.today())
        )
        st.success("New goal saved.")
        st.session_state.step = "done"

# --- STEP: Done ---
elif st.session_state.step == "done":
    st.success("You're all set for today.")
    if st.button("Restart"):
        for key in ["step", "student_id", "student", "background_info"]:
            st.session_state.pop(key, None)
        st.rerun()
