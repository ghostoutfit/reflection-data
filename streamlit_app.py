import streamlit as st
from datetime import datetime, date
import random
from PIL import Image

from goal_bank_loader import (
    load_goal_bank,
    get_goal_text_list,
    get_random_warmup,
    get_gpt_prompt,
    get_config_value
)

from google_sheets import (
    get_student_info,
    create_student_if_missing,
    add_goal_history_entry,
    update_student_current_goal,
    get_goal_history_for_student
)

from openai import OpenAI
openai_client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# --- Session bootstrap ---
if "goal_bank_config" not in st.session_state:
    st.session_state.goal_bank_config = load_goal_bank()
cfg = st.session_state.goal_bank_config

if "step" not in st.session_state:
    st.session_state.step = "enter_id"

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "chat_turn_count" not in st.session_state:
    st.session_state.chat_turn_count = 0

# --- Load student data if missing ---
if "student" not in st.session_state and "student_id" in st.session_state:
    student = get_student_info(st.session_state.student_id)
    if student:
        st.session_state.student = student
    else:
        st.stop()

# --- get motivation case from reflection history ---
def get_motivation_case(goal_history, current_goal, current_reflection, cfg):
    t = cfg.get("motivation_triggers", {})
    low_thresh = t.get("low_follow_threshold", 2)
    repeat_thresh = t.get("repeat_goal_count", 2)
    vague_len = t.get("vague_reflection_length", 10)
    strong_thresh = t.get("strong_streak_threshold", 3)

    recent_entries = goal_history[-3:]

    if sum(1 for entry in recent_entries if entry["GoalAchievement"] in ["0", "1"]) >= low_thresh:
        return "motivation_low_follow"

    if sum(1 for entry in recent_entries if entry["GoalText"] == current_goal) >= repeat_thresh:
        return "motivation_repeat_goal"

    if len(current_reflection.strip()) < vague_len:
        return "motivation_unclear_reflection"

    if sum(1 for entry in recent_entries if entry["GoalAchievement"] in ["3", "4"]) >= strong_thresh:
        return "motivation_strong_streak"

    return None

# --- GPT summarizer fallback ---
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
        st.warning("\u26a0\ufe0f There was an error with the GPT API. We'll still continue.")
        return "shared something about themselves"

# --- Start Streamlit UI ---
st.set_page_config(page_title="Contribution Reflection", layout="centered")
st.title("Class Contribution Reflection")

# --- Main flow control ---
if st.session_state.step == "enter_id":
    student_id_input = st.text_input("Enter your Student ID:")
    if student_id_input:
        student = get_student_info(student_id_input)
        if student:
            for key in ["goal_to_reflect", "background_info", "selected_words", "one_word_options", "current_warmup_prompt"]:
                st.session_state.pop(key, None)

            st.session_state.student_id = student_id_input
            st.session_state.student = student
            st.session_state.step = "warmup"
            st.rerun()
        else:
            st.warning("Student ID not found. Please register below:")
            nickname = st.text_input("Nickname")
            pronoun_code = st.text_input("Pronouns (e.g., she/her, he/him, they/them)")
            chosen_tone = st.selectbox("Preferred Tone", ["Reflective", "Coach", "Challenger"])

            if st.button("Register Student"):
                created = create_student_if_missing(
                    student_id=student_id_input,
                    nickname=nickname,
                    pronoun_code=pronoun_code,
                    tone=chosen_tone
                )
                if created:
                    for key in ["goal_to_reflect", "background_info", "selected_words", "one_word_options", "current_warmup_prompt"]:
                        st.session_state.pop(key, None)
                    st.session_state.student_id = student_id_input
                    st.session_state.student = get_student_info(student_id_input)
                    st.session_state.step = "warmup"
                    st.rerun()
                else:
                    st.error("Student ID already exists.")




# --- STEP 1: WARMUP ---
if st.session_state.step == "warmup" and "student_id" in st.session_state:
    student = st.session_state.student
    nickname = student.get("Nickname", "there")
    st.markdown(f"👋 Hey **{nickname}**, we'll start today's reflection with a quick check-in. "
                "This helps provide background so I can better help you reflect.")
    st.markdown("---")
    st.subheader("Warm Up / Check-In")

    if "current_warmup_prompt" not in st.session_state:
        st.session_state.current_warmup_prompt = get_random_warmup(cfg, "humanizing")

    if "selected_words" not in st.session_state:
        st.session_state.selected_words = []

    if "one_word_options" not in st.session_state:
        st.session_state.one_word_options = random.sample(cfg["warmup_prompts"]["one_word"], k=5)

    st.markdown(f"**{st.session_state.current_warmup_prompt}**")
    response = st.text_input("Your response:")

    st.markdown("---")
    st.markdown("Or choose one or more words that fit your vibe today:")

    cols = st.columns(len(st.session_state.one_word_options))
    for i, word in enumerate(st.session_state.one_word_options):
        if cols[i].button(word, key=f"word_{word}"):
            if word not in st.session_state.selected_words:
                st.session_state.selected_words.append(word)

    if st.session_state.selected_words:
        selected_display = ", ".join(
            f"<span style='color:lightblue;font-weight:bold'>{w}</span>"
            for w in st.session_state.selected_words
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

            # Store warmup as latest reflection (used in onboarding GPT)
            st.session_state.latest_reflection = summary_input

            # Check if onboarding GPT should be triggered
            goal_history = get_goal_history_for_student(st.session_state.student_id)
            if len(goal_history) <= 1:
                st.session_state.motivation_case = "motivation_onboard_intro"
                st.session_state.step = "chatbot_motivation"
                st.rerun()

            # Otherwise proceed to standard flow
            goal_date = student["CurrentGoalSetDate"]
            recent = (
                goal_date and
                (date.today() - datetime.strptime(goal_date, "%Y-%m-%d").date()).days
                <= get_config_value(cfg, "max_days_since_goal", 4)
            )

            if recent:
                st.session_state.goal_to_reflect = {
                    "text": student["CurrentGoal"],
                    "set_date": student["CurrentGoalSetDate"],
                    "source": "app"
                }
                st.session_state.step = "reflect_on_goal"
            else:
                st.session_state.step = "check_manual_goal"

            st.rerun()



    with colB:
        if st.button("Refresh Options"):
            st.session_state.current_warmup_prompt = get_random_warmup(cfg, "humanizing")
            st.session_state.one_word_options = random.sample(cfg["warmup_prompts"]["one_word"], k=5)
            st.rerun()


# --- STEP 2: Reflect on goal (if recent) ---
elif st.session_state.step == "reflect_on_goal":
    goal_info = st.session_state.goal_to_reflect
    st.markdown("### Reflect on Your Goal")
    st.markdown(f"**Goal:** {goal_info['text']}")
    st.markdown(f"**Set On:** {'Today' if goal_info['source'] == 'manual' else goal_info['set_date']}")

    st.markdown("How would you rate your progress toward this goal?")
    goal_achievement = st.radio("", [
        "4 – Met and exceeded",
        "3 – Met goal",
        "2 – Almost met",
        "1 – Tried but didn’t succeed",
        "0 – Didn’t attempt"
    ])
    score_value = int(goal_achievement[0])

    summary_map = {
        4: "Met and exceeded",
        3: "Met goal",
        2: "Almost met",
        1: "Tried but didn’t succeed",
        0: "Didn’t attempt"
    }
    interpretation = summary_map[score_value]
    reflection = st.text_area("What helped or got in the way?")

    if st.button("Submit Reflection"):
        st.session_state.latest_reflection = reflection  # ✅ Store it for later
        add_goal_history_entry({
            "StudentID": st.session_state.student_id,
            "GoalSetDate": goal_info["set_date"],
            "GoalText": goal_info["text"],
            "SuccessMeasure": "[manual goal]" if goal_info["source"] == "manual" else st.session_state.student.get("CurrentSuccessMeasures", ""),
            "OutcomeReflection": reflection,
            "GoalAchievement": score_value,
            "InterpretationSummary": interpretation,
            "BackgroundInfo": st.session_state.student.get("BackgroundInfo", "")
        })

        # ⬇️ Run motivation analysis
        goal_history = get_goal_history_for_student(st.session_state.student_id)
        motivation_case = get_motivation_case(goal_history, goal_info["text"], reflection, cfg)

        if motivation_case:
            st.session_state.motivation_case = motivation_case
            st.session_state.step = "chatbot_motivation"
        else:
            st.session_state.step = "set_contribution_goal"

        st.rerun()




# --- STEP 2B: No recent goal — ask if one was set on paper today ---
elif st.session_state.step == "check_manual_goal":
    st.markdown("### Did you set a goal earlier today (e.g., on paper)?")

    goal_options = get_goal_text_list(cfg)
    selected_goal = st.selectbox("If yes, choose the goal you set:", goal_options)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("That was my goal"):
            st.session_state.goal_to_reflect = {
                "text": selected_goal,
                "set_date": str(date.today()),
                "source": "manual"
            }
            st.session_state.step = "reflect_on_goal"
            st.rerun()

    with col2:
        if st.button("I didn't set a goal"):
            st.session_state.step = "set_contribution_goal"
            st.rerun()

#  --- Chatbot motivational step---
elif st.session_state.step == "chatbot_motivation":
    st.header("Motivation Chat")

    # Show chat history
    for turn in st.session_state.chat_history:
        st.markdown(f"**AI:** {turn['ai']}")
        st.markdown(f"**You:** {turn['user']}")

    # If fewer than 3 turns, continue conversation
    if st.session_state.chat_turn_count < 3:
        user_input = st.text_input("Your reply:", key=f"chat_input_{st.session_state.chat_turn_count}")

        if st.button("Send"):
            # --- Build dynamic system message using context ---
            case = st.session_state.motivation_case
            prompt_instructions = get_gpt_prompt(cfg, case)

            background = st.session_state.student.get("BackgroundInfo", "")
            goal = st.session_state.goal_to_reflect["text"]
            reflection = st.session_state.get("latest_reflection", "")
            user_input = user_input.strip()  # Clean up any stray whitespace

            system_message = (
                "You are a helpful, encouraging motivation coach for high school students.\n\n"
                f"The student has shared this about themselves: {background}\n"
                f"The goal they reflected on was: {goal}\n"
                f"This is what they said about how it went: {reflection}\n\n"
                f"Now continue the conversation. {prompt_instructions}"
            )

            # --- Construct message history with system + prior turns ---
            full_thread = [{"role": "system", "content": system_message}]

            for turn in st.session_state.chat_history:
                full_thread.append({"role": "assistant", "content": turn["ai"]})
                full_thread.append({"role": "user", "content": turn["user"]})

            # Add current user input
            full_thread.append({"role": "user", "content": user_input})

            # --- GPT Completion ---
            try:
                response = openai_client.chat.completions.create(
                    model="gpt-4",
                    messages=full_thread,
                    temperature=0.7,
                    max_tokens=200
                )
                reply = response.choices[0].message.content.strip()
            except Exception as e:
                reply = "⚠️ There was a problem talking to the motivation coach. Want to try again?"

            # --- Update state and rerun ---
            st.session_state.chat_history.append({
                "user": user_input,
                "ai": reply
            })
            st.session_state.chat_turn_count += 1
            st.rerun()


    else:
        st.success("Nice work thinking that through.")

    if st.button("Continue to Goal Setting"):
        st.session_state.step = "set_contribution_goal"
        st.session_state.pop("chat_history", None)
        st.session_state.pop("chat_turn_count", None)
        st.rerun()



# --- STEP 3: Set new goal formally ---
elif st.session_state.step == "set_contribution_goal":
    st.header("Set a goal for next class")

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
        st.rerun()

# --- STEP 4: Wrap up ---
elif st.session_state.step == "done":
    st.success("You're all set for today. See you next time!")
    if st.button("Start Over"):
        for key in ["step", "student_id", "student", "background_info",
                    "current_warmup_prompt", "one_word_options", "selected_words"]:
            st.session_state.pop(key, None)
        st.rerun()
