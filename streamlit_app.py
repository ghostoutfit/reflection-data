import streamlit as st
from datetime import date
from google_sheets import (
    get_student_info,
    create_student_if_missing,
    add_goal_history_entry,
    update_student_current_goal
)
import openai

# --- OpenAI setup ---
openai.api_key = st.secrets["OPENAI_API_KEY"]

from openai import OpenAI

client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

def summarize_background_response(response_text):
    prompt = (
        "Summarize this student response in one sentence, keeping it concise and concrete. "
        "Focus on what the student enjoys, cares about, or finds meaningful:\n\n"
        f'"{response_text}"\n\nSummary:'
    )
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=50
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        st.warning(f"Failed to summarize: {e}")
        return ""


# --- App Setup ---
st.set_page_config(page_title="Class Contribution Reflection", layout="centered")
st.title("Class Contribution Reflection")

# --- Student ID Input ---
student_id = st.text_input("Enter your Student ID:")

if student_id:
    student = get_student_info(student_id)

    if student:
        st.subheader(f"Hi {student['Nickname']}!")
        st.markdown(f"**Tone:** {student['ChosenTone']}")
        st.markdown(f"**Pronouns:** {student['PronounCode']}")
        st.markdown(f"**Background Info:** {student.get('BackgroundInfo', '')}")
        st.markdown("---")

        if not student["CurrentGoal"].strip():
            # --- FIRST GOAL FLOW ---
            st.header("Let’s get to know each other")

            tone = student["ChosenTone"]
            if tone == "Coach":
                prompt = "What's something you're proud of doing lately—in or out of school?"
                st.markdown("👟 *Hey there—ready to set your first goal? Before we jump in, I want to hear a bit about you.*")
            elif tone == "Challenger":
                prompt = "I won’t wait forever. What’s something you're actually into?"
                st.markdown("🔥 *We haven't even set a goal yet. What’s something you’ve done recently that actually felt worth doing?*")
            else:
                prompt = "What's one thing you're curious about or enjoy doing, even if it's small?"
                st.markdown("🌱 *Welcome. Before we set a goal, let’s just check in.*")

            intro_response = st.text_input(prompt)

            if intro_response:
                st.success("Thanks for sharing that.")

                # Summarize and append to BackgroundInfo
                summary = summarize_background_response(intro_response)
                existing_info = student.get("BackgroundInfo", "")
                combined_info = f"{existing_info} | {summary}".strip(" |")

                st.markdown("---")
                st.header("Let’s set your first goal about class contribution")
                first_goal = st.text_area("What’s one way you’d like to contribute more in class?")
                first_success = st.text_area("How will you know you’re succeeding at that?")

                if st.button("Save First Goal"):
                    update_student_current_goal(
                        student_id,
                        new_goal=first_goal,
                        new_success_measures=first_success,
                        set_date=str(date.today()),
                        background_info=combined_info
                    )
                    st.success("Your goal has been saved! You can reflect on it next time.")
                    st.stop()

        else:
            # --- REFLECTION FLOW ---
            st.markdown("### Check-in")
            warmup = st.text_area("Is there anything important going on for you right now?")

            st.markdown("### Reflect on Your Current Goal")
            st.markdown(f"**Current Goal:** {student['CurrentGoal']}")
            st.markdown(f"**Success Measures:** {student['CurrentSuccessMeasures']}")
            st.markdown(f"**Set On:** {student['CurrentGoalSetDate']}")

            outcome_reflection = st.text_area("What happened? Did you meet your goal?")
            goal_achievement = st.selectbox("How would you rate your progress? (0–4)", [
                "4 (Met and Exceeded Goal)",
                "3 (Met Goal)",
                "2 (Almost Met Goal)",
                "1 (Started Toward Goal)",
                "0 (No Real Progress)"
            ])

            score_value = goal_achievement[0]
            summary_map = {
                "4": "Met and Exceeded",
                "3": "Met Goal",
                "2": "Almost Met",
                "1": "Started",
                "0": "No Progress"
            }
            interpretation = summary_map[score_value]

            if st.button("Submit Reflection"):
                add_goal_history_entry({
                    "StudentID": student_id,
                    "GoalSetDate": student["CurrentGoalSetDate"],
                    "GoalText": student["CurrentGoal"],
                    "SuccessMeasure": student["CurrentSuccessMeasures"],
                    "OutcomeReflection": outcome_reflection,
                    "GoalAchievement": score_value,
                    "InterpretationSummary": interpretation,
                    "BackgroundInfo": student.get("BackgroundInfo", "")
                })
                st.success("Reflection added to your history.")

            st.markdown("---")
            st.header("Set a New Goal (Optional)")
            with st.form("new_goal_form"):
                new_goal = st.text_area("New Goal")
                new_success_measure = st.text_area("How will you know you’re succeeding?")
                submitted = st.form_submit_button("Update Current Goal")
                if submitted:
                    update_student_current_goal(
                        student_id,
                        new_goal=new_goal,
                        new_success_measures=new_success_measure,
                        set_date=str(date.today())
                    )
                    st.success("Your new goal has been saved.")


    else:
        st.warning("Student ID not found. Please complete the form below to register.")
        nickname = st.text_input("Nickname")
        pronoun_code = st.text_input("Pronouns (e.g., she/her, he/him, they/them)")
        chosen_tone = st.selectbox("Preferred Tone", ["Reflective", "Coach", "Challenger"])

        if st.button("Register Student"):
            created = create_student_if_missing(
                student_id=student_id,
                nickname=nickname,
                pronoun_code=pronoun_code,
                tone=chosen_tone
            )
            if created:
                student = get_student_info(student_id)
            else:
                st.error("Student ID already exists.")
