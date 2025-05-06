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
def get_motivation_case(goal_history, current_goal, current_reflection, cfg):
    t = cfg.get("motivation_triggers", {})
    low_thresh = t.get("low_follow_threshold", 2)
    repeat_thresh = t.get("repeat_goal_count", 2)
    vague_len = t.get("vague_reflection_length", 10)
    strong_thresh = t.get("strong_streak_threshold", 3)

    recent_entries = goal_history[-3:]

    if sum(1 for entry in recent_entries if str(entry["GoalAchievement"]) in ["0", "1"]) >= low_thresh:
        return "motivation_low_follow"

    if sum(1 for entry in recent_entries if entry["GoalText"] == current_goal) >= repeat_thresh:
        return "motivation_repeat_goal"

    if len(current_reflection.strip()) < vague_len:
        return "motivation_unclear_reflection"

    if sum(1 for entry in recent_entries if str(entry["GoalAchievement"]) in ["3", "4"]) >= strong_thresh:
        return "motivation_strong_streak"

    return None

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

# --- Demo / Test mode to try out AI conversation
if st.session_state.get("step") == "enter_id":
    if st.button("OR Try AI Chat Demo with sample goal data"):
        st.session_state.step = "chatbot_motivation"
        st.session_state.goal_to_reflect = {"source": "demo"}  # triggers special behavior
        st.rerun()


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

    summary_map = {
        4: "Met and exceeded",
        3: "Met goal",
        2: "Almost met",
        1: "Tried but didn’t succeed",
        0: "Didn’t attempt"
    }
    interpretation = summary_map[score_value]
    reflection = st.text_area("What helped or got in the way?")

    if st.button("Submit Reflection", key="submit_reflection1"):    # Don't know why we need two of these, but we do?
        st.session_state.latest_reflection = reflection  # ✅ Store it for later
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

        # --- Regenerate backgroundinfo summary from past reflections ---
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

        # --- After user submits reflection and before motivation check ---
        # --- There are two Submit Reflection buttons, but if I remove one we get issues ugh  ---
        if st.button("Submit Reflection", key="submit_reflection2"):
            st.session_state.latest_reflection = reflection
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

            # ✅ Update BackgroundInfo from history
            new_summary = regenerate_background_summary_from_history(st.session_state.student_id)
            update_student_current_goal(
                student_id=st.session_state.student_id,
                new_goal=st.session_state.student.get("CurrentGoal", ""),
                new_success_measures=st.session_state.student.get("CurrentSuccessMeasures", ""),
                set_date=st.session_state.student.get("CurrentGoalSetDate", str(date.today())),
                background_info=new_summary
            )
            st.session_state.background_info = new_summary

            goal_history = get_goal_history_for_student(st.session_state.student_id)
            motivation_case = get_motivation_case(goal_history, goal_info["text"], reflection, cfg)

            if motivation_case:
                st.session_state.motivation_case = motivation_case
                st.session_state.step = "chatbot_motivation"
            else:
                st.session_state.step = "set_contribution_goal"

            st.rerun()



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

    goal_info = st.session_state.get("goal_to_reflect", {})

    if goal_info.get("source") == "onboard":
        st.session_state.student_id = st.session_state.get("student_id")
        st.session_state.student = get_student_info(st.session_state.student_id)
        st.session_state.latest_reflection = st.session_state.get("latest_reflection", "")
        st.session_state.motivation_case = "motivation_onboard_intro"


    # Engage Demo Mode to try out AI chat    
    if goal_info.get("source") == "demo":
        # Only choose and store a random entry once
        if "demo_loaded" not in st.session_state:
            sheet = get_sheet("GoalHistory")
            records = sheet.get_all_records()
            import random
            random_entry = random.choice(records)

            st.session_state.random_demo_entry = random_entry  # ✅ store the row persistently
            st.session_state.demo_loaded = True
        else:
            random_entry = st.session_state.random_demo_entry  # ✅ retrieve the stored row

        # Set student info
        st.session_state.student_id = str(random_entry["StudentID"])
        student_record = get_student_info(st.session_state.student_id)
        student_record["BackgroundInfo"] = random_entry.get("BackgroundInfo", "")
        st.session_state.student = student_record

        # Set goal and reflection info
        st.session_state.goal_to_reflect = {
            "text": random_entry["GoalText"],
            "set_date": random_entry["GoalSetDate"],
            "source": "demo"
        }
        st.session_state.latest_reflection = random_entry.get("OutcomeReflection", "")

        # Calculate motivation case for the demo record
        demo_history = get_goal_history_for_student(st.session_state.student_id)
        st.session_state.motivation_case = get_motivation_case(
            goal_history=demo_history,
            current_goal=random_entry["GoalText"],
            current_reflection=random_entry["OutcomeReflection"],
            cfg=cfg
        )

        # Display the summary to the user
        nickname = student_record.get("Nickname", "[unknown]")
        recent_goal = random_entry.get("GoalText", "[no goal yet]")
        background = random_entry.get("BackgroundInfo", "[none]")

        st.markdown(f"**Your name is:** {nickname}")
        st.markdown(f"**Your most recent goal is:** {recent_goal}")
        prompt_text = get_gpt_prompt(cfg, st.session_state.motivation_case)
        abbreviated_prompt = prompt_text[:200] + "..." if len(prompt_text) > 200 else prompt_text
        st.markdown(f"**The AI chose this prompt, based on reflection history:** `{abbreviated_prompt}`")
        st.markdown(f"**Background info from previous reflections includes:** {background}")
        st.markdown("---")
             
   # Recalculate motivation case for the demo record
    if goal_info.get("source") == "demo":
        demo_history = get_goal_history_for_student(st.session_state.student_id)
        st.session_state.motivation_case = get_motivation_case(
            goal_history=demo_history,
            current_goal=random_entry["GoalText"],
            current_reflection=random_entry["OutcomeReflection"],
            cfg=cfg
        )
    print(f"[DEMO] Motivation case selected: {st.session_state.motivation_case}")
    
    st.header("Reflect with an AI:")

    if st.session_state.motivation_case == "motivation_onboard_intro":
        st.markdown("##### 🤖  I'm a robot designed to help you meet your goals in this class... 🤓")
    
    # Show chat history
    # DEBUG - I can't get rid of the first "you" even though we don't input anything
    for turn in st.session_state.chat_history:
        if st.session_state.chat_turn_count > 1:
            st.markdown(f"**You:** {turn['user']}")
        st.markdown(f"**AI:** {turn['ai']}")


    
    # If fewer than 3 turns, continue conversation
    if st.session_state.chat_turn_count < 3:
        
        if st.session_state.chat_turn_count == 0:
            user_input = ""
        else:
            user_input = st.text_input("Your reply:", key=f"chat_input_{st.session_state.chat_turn_count}")

        st.markdown("##### Would you prefer my next response to be:")
        col1, col2, col3 = st.columns(3)

        def handle_chat_reply(length_label):
            case = st.session_state.get("motivation_case", "motivation_onboard_intro")
            prompt_instructions = get_gpt_prompt(cfg, case)
            background = st.session_state.student.get("BackgroundInfo", "")
            goal = st.session_state.get("goal_to_reflect", {}).get("text", "[No goal yet]")
            reflection = st.session_state.get("latest_reflection", "")
            user_input_clean = user_input.strip()

            # 👇 Add this line to log to the terminal
            history = get_goal_history_for_student(st.session_state.student_id)
            print(f"[DEBUG] Student {st.session_state.student_id} has {len(history)} goal history entries.")
            print(f"[GPT Prompt Case] Using prompt case: {case}")
            print(f"[Prompt Text] {prompt_instructions[:200]}...")  # optional: only show beginning for brevity

            length_pref_map = {
                "short": "Respond briefly, in 2–3 brief sentences, using simple words.",
                "long": "Respond with moderate detail, around 2–4 sentences.",
            }
            length_pref = length_pref_map.get(length_label, "")

            system_message = (
                "You are a helpful, encouraging motivation coach for high school students.\n\n"
                f"The student has shared this about themselves: {background}\n"
                f"The goal they reflected on was: {goal}\n"
                f"This is what they said about how it went: {reflection}\n\n"
                f"{length_pref}\n\n"
                f"Now continue the conversation. {prompt_instructions}"
            )

            full_thread = [{"role": "system", "content": system_message}]
            for turn in st.session_state.chat_history:
                full_thread.append({"role": "assistant", "content": turn["ai"]})
                full_thread.append({"role": "user", "content": turn["user"]})
            full_thread.append({"role": "user", "content": user_input_clean})

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

            st.session_state.chat_history.append({
                "user": user_input_clean,
                "ai": reply
            })
            st.session_state.chat_turn_count += 1
            st.rerun()

        with col1:
            if st.button("Shorter"):
                handle_chat_reply("short")
        with col2:
            if st.button("Longer"):
                handle_chat_reply("long")



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
