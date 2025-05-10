import streamlit as st
import pandas as pd
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
    get_goal_history_for_student,
    get_sheet
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
#def get_motivation_case(goal_history, current_goal, current_reflection, cfg):
#    t = cfg.get("motivation_triggers", {})
#    low_thresh = t.get("low_follow_threshold", 2)
#    repeat_thresh = t.get("repeat_goal_count", 2)
#    vague_len = t.get("vague_reflection_length", 10)
#    strong_thresh = t.get("strong_streak_threshold", 3)
#
#    recent_entries = goal_history[-3:]
#
#    if sum(1 for entry in recent_entries if str(entry["GoalAchievement"]) in ["0", "1"]) >= low_thresh:
#        return "motivation_low_follow"
#
#    if sum(1 for entry in recent_entries if entry["GoalText"] == current_goal) >= repeat_thresh:
#        return "motivation_repeat_goal"
#
#    if len(current_reflection.strip()) < vague_len:
#        return "motivation_unclear_reflection"
#
#    if sum(1 for entry in recent_entries if str(entry["GoalAchievement"]) in ["3", "4"]) >= strong_thresh:
#        return "motivation_strong_streak"
#
#    return None

def choose_next_step_from_goal_history(student_id, current_goal, current_reflection, goal_date, cfg, goal_source="app"):
    history = get_goal_history_for_student(student_id)
    print(f"[DEBUG step routing] Student {student_id} has {len(history)} goal history entries.")
    
    recent = (
        goal_date and
        (date.today() - datetime.strptime(goal_date, "%Y-%m-%d").date()).days
        <= get_config_value(cfg, "max_days_since_goal", 4)
    )

    if len(history) < 1:
        st.session_state.motivation_case = "motivation_onboard_intro"
        st.session_state.step = "chatbot_motivation"
    elif recent:
        st.session_state.goal_to_reflect = {
            "text": current_goal,
            "set_date": goal_date,
            "source": goal_source
        }
        st.session_state.step = "reflect_on_goal"
    else:
        st.session_state.step = "check_manual_goal"


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

# These two prompts form the core "Reflection Chat" experience
# Users choose between a "Nicer" and a "Tougher" bot for their reflection conversation.
def build_real_one_prompt(goal, score_value, interpretation, reflection, background, length_pref, score_behavior_instruction):
    return f"""
You talk like someone who actually cares but hates fake school conversations. You keep it real.
You don’t flatter, but you notice effort. You speak plainly, ask real questions, and don’t push too hard.
You sound like someone worth talking to. Every reply should feel like a conversation you'd actually have with a smart, tired 9th grader.

The student reflected on a goal. Here’s what they shared:
- **Goal:** {goal}
- **Self-assessment (0–4):** {score_value} – {interpretation}
- **Reflection on what helped or got in the way:** "{reflection}"
- **Background info (less important):** {background}

The student scored themselves a {score_value} out of 4.
{score_behavior_instruction}

{length_pref}

In all conversations, your job is to:
- Pick up on anything real or meaningful in their reflection
- Ask follow-up quesrtions that feel honest, curious, and grounded
- If they’re stuck, suggest one or two low-pressure things they might try differently next time
- Be concise and readable. No pep talks. No fake cheer. No therapy voice.

Possible goals include: 
    Goal 1: Every time we are asked to turn and talk, I will say one thing on topic to my partner.
    Goal 2: Every time we talk as a group, I will contribute at least 1 idea or ask 1 question.
    Goal 3: Raise my hand and make contributions or ask questions.
    Goal 4: Give my ideas to someone else, then encourage others to share them out.
    Goal 5: Ask people what they think, and use “3 before me”
    Goal 6: Ask questions to the teacher or small group when I get lost.
    Goal 7: Use the phrase, “I am only 20% sure…” to let people know I am taking a risk.
    Goal 8: Say something on topic to my shoulder partner, and ask them to share it for me.
    Goal 9: Stay engaged in on topic conversation with my group members at every opportunity.
""".strip()


def build_drill_sergeant_prompt(goal, score_value, interpretation, reflection, background, length_pref, score_behavior_instruction):
    return f"""
You are here to push the student to improve. You are sharp, exacting, and focused on results.
If the student gives a vague or weak answer, call it out—briefly and clearly. Then push them to think harder.
End each message with a direct challenge—but not just an action. Sometimes your challenge should ask: Why does this matter to you? What are you trying to prove?

You are not soft. You are not friendly. You don’t offer fake encouragement. You offer pressure, precision, and questions that leave no place to hide.

The student reflected on a goal. Here’s what they shared:
- **Goal:** {goal}
- **Self-assessment (0–4):** {score_value} – {interpretation}
- **Reflection on what helped or got in the way:** "{reflection}"
- **Background info (less important):** {background}

The student scored themselves a {score_value} out of 4.
{score_behavior_instruction}

{length_pref}

In all conversations, your job is to:
- Pick up on anything real or meaningful in their reflection
- Ask a follow-up that feels honest, curious, and grounded
- If they’re stuck, suggest one or two low-pressure things they might try differently next time
- Be concise and readable. No pep talks. No fake cheer. No therapy voice.

Possible goals include: 
    Goal 1: Every time we are asked to turn and talk, I will say one thing on topic to my partner.
    Goal 2: Every time we talk as a group, I will contribute at least 1 idea or ask 1 question.
    Goal 3: Raise my hand and make contributions or ask questions.
    Goal 4: Give my ideas to someone else, then encourage others to share them out.
    Goal 5: Ask people what they think, and use “3 before me”
    Goal 6: Ask questions to the teacher or small group when I get lost.
    Goal 7: Use the phrase, “I am only 20% sure…” to let people know I am taking a risk.
    Goal 8: Say something on topic to my shoulder partner, and ask them to share it for me.
    Goal 9: Stay engaged in on topic conversation with my group members at every opportunity.
""".strip()


# --- Start Streamlit UI ---
st.set_page_config(page_title="Contribution Reflection", layout="centered")
st.title("Class Contribution Reflection")

# --- Main flow control ---
if st.session_state.step == "enter_id":

    # Load the sheet for sample display
    sheet = get_sheet("Students")
    records = sheet.get_all_records()
    df = pd.DataFrame(records)

    ref_df = df[["StudentID", "Nickname", "PronounCode", "BackgroundInfo"]].rename(
        columns={"StudentID": "ID", "PronounCode": "P"}
    )

    # Instructional info for testers
    st.markdown("""
    ---
    #### 👋 Welcome to the Reflection App Demo

    You can:
    - **Enter a new "Student ID" to test onboarding**
    - Or pick an ID from the table below and reflect as that student
    """)

    student_id_input = st.text_input("Enter your Student ID:")


    col1, col2 = st.columns(2)

    with col1:
        if st.button("Reflect as an existing student"):
            if student_id_input.strip():
                    student = get_student_info(student_id_input.strip())
                    if student:
                        st.session_state.student_id = student_id_input.strip()
                        st.session_state.student = student
                        st.session_state.goal_to_reflect = {
                            "text": student.get("CurrentGoal", "[no goal set]"),
                            "set_date": student.get("CurrentGoalSetDate", str(date.today())),
                            "source": "demo"
                        }
                        st.session_state.step = "reflect_on_goal"
                        st.rerun()
            else:
                st.warning("That ID doesn't exist. Pick one from the table below or onboard a new one.")

    with col2:
        if st.button("Onboard a new student"):
            clean_id = student_id_input.strip()
            if not clean_id:
                st.warning("Please enter a student ID before onboarding.")
            elif get_student_info(clean_id):
                st.error("❌ That student ID already exists. Try an ID that's not in the table, or click *Reflect as an existing student.*")
            else:
                st.session_state.step = "onboard_student"
                st.session_state.new_student_id = clean_id


    # ↓ Tester guidance and table
    st.markdown("""
    ---
    #### 🔍 Student Reference Table
    Pick any ID from this table and enter it above to try out the app as that student.
    """)
    st.dataframe(ref_df.reset_index(drop=True), use_container_width=True)



# --- STEP 1: WARMUP ---
if st.session_state.step == "warmup" and "student_id" in st.session_state:
    student = st.session_state.student
    nickname = student.get("Nickname", "there")
    
    # First-time users: collect deeper background info
    goal_history = get_goal_history_for_student(st.session_state.student_id)
    if len(goal_history) == 0 and "background_collected" not in st.session_state:
        st.header(f"Hi {nickname}, I’d like to get to know you a bit.")

        st.markdown(
            "🧠 *It’ll be easier to help make your reflections meaningful if I know something about what you care about.*"
        )
        bio_input = st.text_area("What do you want me to know about you?")

        if st.button("Continue"):
            # Store the student’s own words, unaltered
            raw_bio = bio_input.strip()
            existing_info = student.get("BackgroundInfo", "")
            combined_info = f"{existing_info} | {raw_bio}".strip(" |")

            if not existing_info.strip():
                update_student_current_goal(
                    student_id=st.session_state.student_id,
                    new_goal=student.get("CurrentGoal", ""),
                    new_success_measures=student.get("CurrentSuccessMeasures", ""),
                    set_date=student.get("CurrentGoalSetDate", str(date.today())),
                    background_info=raw_bio  # ⬅️ student’s own answer, not a summary
                )
                # Update local session copy with fresh data from sheet
                st.session_state.student = get_student_info(st.session_state.student_id)



            st.session_state.background_info = combined_info
            st.session_state.latest_reflection = bio_input
            st.session_state.background_collected = True  # prevent re-asking
            st.rerun()

        st.stop()  # Hold until they finish this input
    
    
    if len(goal_history) == 0:
        st.markdown(f"We'll start every reflection with a quick check-in. "
                    "This helps provide background so I can better help you reflect.")
    else:
        st.markdown(f"👋 Hey **{nickname}**, we'll start today's reflection with a quick check-in. "
                    "This helps provide background so I can better help you reflect.")
    st.subheader("Warm Up / Check-In")

    if "current_warmup_prompt" not in st.session_state:
        st.session_state.current_warmup_prompt = get_random_warmup(cfg, "humanizing")

    if "selected_words" not in st.session_state:
        st.session_state.selected_words = []

    if "one_word_options" not in st.session_state:
        st.session_state.one_word_options = random.sample(cfg["warmup_prompts"]["one_word"], k=5)

    st.markdown(f"**{st.session_state.current_warmup_prompt}**")
    response = st.text_input("Your response:")

    st.markdown("AND / OR")
    st.markdown("Choose one or more words that fit how you're feeling today:")

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
            summary_input = ""
            if response:
                summary_input += response.strip()
            if st.session_state.selected_words:
                words = ", ".join(st.session_state.selected_words)
                if summary_input:
                    summary_input += f" (Also described themselves as: {words})"
                else:
                    summary_input = f"Described themselves as: {words}"

            st.session_state.latest_reflection = summary_input

            existing_info = student.get("BackgroundInfo", "").strip()

            if not existing_info:
                # First-time response: store raw text
                st.session_state.background_info = summary_input
                update_student_current_goal(
                    student_id=st.session_state.student_id,
                    new_goal=student.get("CurrentGoal", ""),
                    new_success_measures=student.get("CurrentSuccessMeasures", ""),
                    set_date=student.get("CurrentGoalSetDate", str(date.today())),
                    background_info=summary_input
                )
            else:
                # Pull past reflections from GoalHistory
                history = get_goal_history_for_student(st.session_state.student_id)
                past_reflections = [entry.get("BackgroundInfo", "") for entry in history if entry.get("BackgroundInfo", "").strip()]

                # Combine everything into one string
                combined_input = " | ".join([existing_info] + past_reflections + [summary_input])

                # Summarize with GPT
                background_summary = summarize_background_response(combined_input)

                # Save back to Students sheet
                st.session_state.background_info = background_summary
                update_student_current_goal(
                    student_id=st.session_state.student_id,
                    new_goal=student.get("CurrentGoal", ""),
                    new_success_measures=student.get("CurrentSuccessMeasures", ""),
                    set_date=student.get("CurrentGoalSetDate", str(date.today())),
                    background_info=background_summary
                )

            # Refresh local student record for use in GPT
            st.session_state.student = get_student_info(st.session_state.student_id)

            # Continue with next step logic (chatbot onboarding, recent goal, etc.)
            goal_date = student["CurrentGoalSetDate"]
            recent = (
                goal_date and
                (date.today() - datetime.strptime(goal_date, "%Y-%m-%d").date()).days
                <= get_config_value(cfg, "max_days_since_goal", 4)
            )

            # If first-time user, jump straight to chatbot onboarding
            if len(goal_history) == 0:
                st.session_state.goal_to_reflect = {
                    "text": "[No goal yet]",
                    "set_date": str(date.today()),
                    "source": "onboard"
                }
                st.session_state.motivation_case = "motivation_onboard_intro"
                st.session_state.step = "chatbot_motivation"
            else:
                # Fall back to goal-based routing
                choose_next_step_from_goal_history(
                    student_id=st.session_state.student_id,
                    current_goal=student["CurrentGoal"],
                    current_reflection=summary_input,
                    goal_date=student["CurrentGoalSetDate"],
                    cfg=cfg,
                    goal_source="app"
                )

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

    # Calculate motivation case early if not already set
    #if "motivation_case" not in st.session_state:
    #    goal_history = get_goal_history_for_student(st.session_state.student_id)
    #    reflection = st.session_state.get("latest_reflection", "")  # fallback to blank if not submitted yet
    #    motivation_case = get_motivation_case(
    #        goal_history=goal_history,
    #        current_goal=goal_info["text"],
    #        current_reflection=reflection,
    #        cfg=cfg
    #    )
    #    st.session_state.motivation_case = motivation_case or None


    if goal_info.get("source") == "demo":
        #pull summary info
        nickname = st.session_state.student.get("Nickname", "[unknown]")
        student = st.session_state.student
        background = student.get("BackgroundInfo", "[none]")


        st.markdown("*Demo Mode! This is a pre-filled student for testing purposes.*")
        #st.markdown("**Conditional Reflection Prompts:** Students who have A) chosen the same goal three times in a row or B) repeatedly struggled to meet straightforward goals are guided toward a different prompt. Based on initial testing, we chose a *Drill Sergeant*-type prompt, but for now this is just to show proof of concept.")
        #st.markdown("Students who have meet other conditions are given other prompts.")
        
        # Display the summary to the user
        st.markdown("##### Student Persona:")
        st.markdown(f"**Your name is:** {nickname}")
        st.markdown(f"**Your most recent goal is:** {goal_info.get('text', '[no goal]')}")
        # Choose motivation case based on goal history
        #motivation_case = st.session_state.get("motivation_case", None)
        #if motivation_case:
        #    prompt_text = get_gpt_prompt(cfg, motivation_case)
        #else:
        #    prompt_text = "[No prompt selected yet]"
        #abbreviated_prompt = prompt_text[:200] + "..." if len(prompt_text) > 200 else prompt_text
        #st.markdown(f"**The AI chose this prompt, based on reflection history:** `{abbreviated_prompt}`")
        #st.markdown(f"**Background info from previous reflections includes:** {background}")
        #st.markdown("---")    
    
    st.markdown("### Reflect on Your Goal")
    st.markdown(f"**Goal:** {goal_info['text']}")
    st.markdown(f"**Set On:** {'Today' if goal_info['source'] == 'manual' else goal_info['set_date']}")
    if goal_info["source"] != "manual":
        success = st.session_state.student.get("CurrentSuccessMeasures", "")
        st.markdown(f"**Success on this goal looks like:** {success}")

    goal_achievement = st.radio("How would you rate your progress toward this goal?", [
        "4 – Met and exceeded",
        "3 – Met goal",
        "2 – Almost met",
        "1 – Tried but didn’t succeed",
        "0 – Didn’t attempt"
    ])
    score_value = int(goal_achievement[0])
    st.session_state.latest_score_value = score_value

    summary_map = {
        4: "Met and exceeded",
        3: "Met goal",
        2: "Almost met",
        1: "Tried but didn’t succeed",
        0: "Didn’t attempt"
    }
    interpretation = summary_map[score_value]
    reflection = st.text_area("What helped or got in the way?")

    if st.button("Submit Reflection", key="submit_reflection1"):
        st.session_state.latest_reflection = reflection

        add_goal_history_entry({
            "StudentID": st.session_state.student_id,
            "GoalSetDate": goal_info["set_date"],
            "GoalText": goal_info["text"],
            "SuccessMeasures": "[manual goal]" if goal_info["source"] == "manual" else st.session_state.student.get("CurrentSuccessMeasures", ""),
            "OutcomeReflection": reflection,
            "GoalAchievement": score_value,
            "InterpretationSummary": interpretation,
            "BackgroundInfo": st.session_state.student.get("BackgroundInfo", "")
        })

        # --- Regenerate BackgroundInfo from history ---
        def regenerate_background_summary_from_history(student_id):
            history = get_goal_history_for_student(student_id)
            warmup_texts = [
                entry["OutcomeReflection"]
                for entry in history
                if entry.get("OutcomeReflection") and len(entry["OutcomeReflection"].strip()) > 5
            ]
            full_text = "\n".join(warmup_texts[-10:])  # Limit to last 10 entries

            prompt = (
                "Summarize the student's interests, feelings, and experiences based on these past reflections:\n\n"
                f"{full_text.strip()}\n\nSummary:"
            )

            try:
                response = openai_client.chat.completions.create(
                    model="gpt-4",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.5,
                    max_tokens=60
                )
                return response.choices[0].message.content.strip()
            except Exception:
                return "Summary unavailable"

        new_summary = regenerate_background_summary_from_history(st.session_state.student_id)
        update_student_current_goal(
            student_id=st.session_state.student_id,
            new_goal=st.session_state.student.get("CurrentGoal", ""),
            new_success_measures=st.session_state.student.get("CurrentSuccessMeasures", ""),
            set_date=st.session_state.student.get("CurrentGoalSetDate", str(date.today())),
            background_info=new_summary
        )
        st.session_state.background_info = new_summary

        # ⬇️ Run motivation analysis
        #goal_history = get_goal_history_for_student(st.session_state.student_id)
        #motivation_case = get_motivation_case(goal_history, goal_info["text"], reflection, cfg)

        #if motivation_case:
        #    st.session_state.motivation_case = motivation_case
        #    st.session_state.step = "chatbot_motivation"
        #else:
        #    st.session_state.step = "set_contribution_goal"
        #st.rerun()

        # ➡️ Route directly to the chatbot reflection step
        st.session_state.step = "chatbot_motivation"
        st.rerun()

# Onboard a new student
elif st.session_state.step == "onboard_student":
    st.header("Register a New Student")

    student_id = st.session_state.get("new_student_id", "")
    st.markdown(f"**Student ID:** `{student_id}`")

    nickname = st.text_input("Nickname")
    pronoun_code = st.text_input("Pronouns (e.g., she/her, he/him, they/them)")
    chosen_tone = st.selectbox("Preferred Tone", ["Reflective", "Coach", "Challenger"])

    if st.button("Register"):
        # ✅ Check if this student ID already exists
        if get_student_info(student_id):
            st.error("❌ That student ID already exists. Please choose a different one.")
        else:
            created = create_student_if_missing(
                student_id=student_id,
                nickname=nickname,
                pronoun_code=pronoun_code,
                tone=chosen_tone
            )
            if created:
                st.session_state.student_id = student_id
                st.session_state.student = get_student_info(student_id)
                st.session_state.step = "warmup"
                st.rerun()
            else:
                st.error("⚠️ Something went wrong creating the student. Please try again.")


# --- STEP 2B: No recent goal — ask if one was set on paper today ---
elif st.session_state.step == "check_manual_goal":
    st.markdown("### Did you set a goal earlier today (e.g., on paper)?")

    goal_options = get_goal_text_list(cfg)
    selected_goal = st.selectbox("If yes, choose the goal you set:", goal_options)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Choose This Goal"):
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

    goal_info = st.session_state.get("goal_to_reflect", {})
    student = st.session_state.student
    background = student.get("BackgroundInfo", "[none]")
    goal = goal_info.get("text", "[No goal]")
    reflection = st.session_state.get("latest_reflection", "")
    score_value = st.session_state.get("latest_score_value", 0)
    interpretation_map = {
        4: "Met and exceeded",
        3: "Met goal",
        2: "Almost met",
        1: "Tried but didn’t succeed",
        0: "Didn’t attempt"
    }
    interpretation = interpretation_map.get(score_value, "Not rated")
    #case = st.session_state.get("motivation_case", None)

    def handle_chat_reply(length_label, user_input=""):
        tone = st.session_state.get("tone_pref", "real_one")
        length_pref_map = {
            "short": "Respond briefly, in 2–3 short sentences.",
            "long": "Respond with moderate detail, around 3–5 sentences.",
        }
        length_pref = length_pref_map.get(length_label, "")

        score_behavior_instruction = (
            "They scored a 0, 1, or 2. Your job is to help them find strategies to meet this goal in the future."
            if score_value <= 2 else
                "They scored a 3 or 4. Your job is to acknowledge their success, and find a different goal to help them grow."
            )


        # First turn: synthesize user_input
        if st.session_state.chat_turn_count == 0:
            user_input_clean = (
                f"I was working on the goal: '{goal}'. "
                f"I gave myself a score of {score_value} — {interpretation}. "
                f"What helped or got in the way: {reflection}"
            )
        else:
            user_input_clean = user_input.strip()

        if tone == "drill_sergeant":
            system_message = build_drill_sergeant_prompt(goal, score_value, interpretation, reflection, background, length_pref, score_behavior_instruction)
        else:
            system_message = build_real_one_prompt(goal, score_value, interpretation, reflection, background, length_pref, score_behavior_instruction)

        # Assemble GPT thread
        full_thread = [{"role": "system", "content": system_message}]
        for turn in st.session_state.chat_history:
            full_thread.append({"role": "assistant", "content": turn["ai"]})
            if "user" in turn:
                full_thread.append({"role": "user", "content": turn["user"]})
        full_thread.append({"role": "user", "content": user_input_clean})

        # Get AI response
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4",
                messages=full_thread,
                temperature=0.7,
                max_tokens=200
            )
            reply = response.choices[0].message.content.strip()
        except Exception:
            reply = "⚠️ There was a problem talking to the goal-setting chatbot. Want to try again?"

        # Store conversation
        if st.session_state.chat_turn_count == 0:
            st.session_state.chat_history.append({
                "ai": reply  # no user entry on first turn
            })
        else:
            st.session_state.chat_history.append({
                "user": user_input_clean,
                "ai": reply
            })

        st.session_state.chat_turn_count += 1
        st.rerun()

    # --- First Turn: Tone selection only ---
    if st.session_state.chat_turn_count == 0:
        st.header("Reflect with an AI:")
        st.markdown("### Choose a style for how I respond:")
        col1, col2 = st.columns(2)

        with col1:
            if st.button("🟢 Nicer"):
                st.session_state.tone_pref = "real_one"
                handle_chat_reply("long")

        with col2:
            if st.button("🔴 Tougher"):
                st.session_state.tone_pref = "drill_sergeant"
                handle_chat_reply("long")

        # Allow skipping AI reflection
        st.markdown("---")
        if st.button("Or skip AI and set your goal now"):
            st.session_state.step = "set_contribution_goal"
            st.session_state.pop("chat_history", None)
            st.session_state.pop("chat_turn_count", None)
            st.rerun()


    # --- Turns 1 and 2: Regular interaction ---
    elif st.session_state.chat_turn_count < 3:
        st.header("Reflect with an AI:")

        # Show system prompt label
        tone = st.session_state.get("tone_pref", "real_one")
        prompt_label = {
            "real_one": "Real One (Nicer)",
            "drill_sergeant": "Drill Sergeant (Tougher)"
        }.get(tone, "Unknown Style")

        st.markdown(f"<span style='color:green'><b>System Prompt:</b> {prompt_label}</span>", unsafe_allow_html=True)


        # Show conversation history
        for i, turn in enumerate(st.session_state.chat_history):
            if i > 0 and "user" in turn:
                st.markdown(f"**You:** {turn['user']}")
            st.markdown(f"**AI:** {turn['ai']}")

        user_input = st.text_input("Your reply:", key=f"chat_input_{st.session_state.chat_turn_count}")
        col1, col2 = st.columns(2)

        with col1:
            if st.button("Shorter Response", key=f"short_{st.session_state.chat_turn_count}"):
                handle_chat_reply("short", user_input)

        with col2:
            if st.button("Longer Response", key=f"long_{st.session_state.chat_turn_count}"):
                handle_chat_reply("long", user_input)
        
        # Allow skipping AI reflection
        st.markdown("---")
        if st.button("or skip to set your goal now"):
            st.session_state.step = "set_contribution_goal"
            st.session_state.pop("chat_history", None)
            st.session_state.pop("chat_turn_count", None)
            st.rerun()

    # --- Turn 3: Wrap up ---
    else:
        # Show final conversation
        for i, turn in enumerate(st.session_state.chat_history):
            if i > 0 and "user" in turn:
                st.markdown(f"**You:** {turn['user']}")
            st.markdown(f"**AI:** {turn['ai']}")

        st.success("Nice work thinking that through. If the AI just asked you a new question, keep it in mind as you set your next goal.")
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
    measure = st.text_area("What will it look like when you're succeeding?")

    if st.button("Set Goal"):
        update_student_current_goal(
            student_id=st.session_state.student_id,
            new_goal=final_goal,
            new_success_measures=measure,
            set_date=str(date.today())
        )

        # Check if this is the first goal ever
        history = get_goal_history_for_student(st.session_state.student_id)
        if len(history) == 0:
            # Log initial goal into GoalHistory
            from datetime import date
            today = date.today().isoformat()  # Returns '2025-05-07' format
            add_goal_history_entry({
                "StudentID": st.session_state.student_id,
                "GoalSetDate": today,
                "Goal": final_goal,
                "SuccessMeasures": measure,
                "OutcomeReflection": "[first goal]",
                "GoalAchievement": "[first goal]",
                "BackgroundInfo": st.session_state.student.get("BackgroundInfo", "")
            })

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
