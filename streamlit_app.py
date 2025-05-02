import streamlit as st
from datetime import date
from google_sheets import (
    get_student_info,
    create_student_if_missing,
    add_goal_history_entry,
    update_student_current_goal
)

# --- 1. App Setup ---
st.set_page_config(page_title="Class Contribution Reflection", layout="centered")
st.title("Class Contribution Reflection")

# --- 2. Student ID Input ---
student_id = st.text_input("Enter your Student ID:")

if student_id:
    student = get_student_info(student_id)

    # --- 3a. Student FOUND: Display and use data ---
    if student:
        st.subheader(f"Hi {student['Nickname']}!")
        st.markdown(f"**Preferred Tone:** {student['ChosenTone']}")
        st.markdown(f"**Pronouns:** {student['PronounCode']}")
        st.markdown(f"**Current Goal:** {student['CurrentGoal']}")
        st.markdown(f"**Success Measures:** {student['CurrentSuccessMeasures']}")
        st.markdown(f"**Goal Range:** {student['GoalRange']}")
        st.markdown("---")

        # --- 4. Reflect on Current Goal ---
        st.header("Reflect on Your Current Goal")
        outcome_reflection = st.text_area("What happened? Did you meet your goal?")
        goal_achievement = st.selectbox("How would you rate your progress?", [
            "+2 (Far exceeded)",
            "+1 (Slightly exceeded)",
            "0 (Met expectations)",
            "-1 (Almost met)",
            "-2 (Did not meet)"
        ])
        interpretation = goal_achievement.split(" ", 1)[1]
        score_value = goal_achievement.split(" ")[0]

        if st.button("Submit Reflection"):
            add_goal_history_entry({
                "StudentID": student_id,
                "GoalSetDate": student["CurrentGoalSetDate"],
                "GoalText": student["CurrentGoal"],
                "SuccessMeasure": student["CurrentSuccessMeasures"],
                "OutcomeReflection": outcome_reflection,
                "GoalAchievement": score_value,
                "InterpretationSummary": interpretation
            })
            st.success("Reflection added to your history.")

        st.markdown("---")

        # --- 5. Optional: Set a New Goal ---
        st.header("Set a New Goal (Optional)")
        with st.form("new_goal_form"):
            new_goal = st.text_area("New Goal")
            new_success_measure = st.text_area("How will you know you’re succeeding?")
            new_goal_range = st.selectbox("New Goal Range (optional)", [
                "", "-2 to +2", "0 to +2", "0 to 1"
            ])
            submitted = st.form_submit_button("Update Current Goal")
            if submitted:
                update_student_current_goal(
                    student_id,
                    new_goal=new_goal,
                    new_success_measures=new_success_measure,
                    set_date=str(date.today()),
                    goal_range=new_goal_range if new_goal_range else None
                )
                st.success("Your current goal was updated.")

    # --- 3b. Student NOT FOUND: Register New Student ---
    else:
        st.warning("Student ID not found.")
        with st.expander("Create New Student Record"):
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
                        st.success("Student registered. Please re-enter your Student ID to continue.")
                    else:
                        st.error("Student ID already exists.")
    