import pandas as pd
from datetime import date, timedelta
import random

# Fixed data for goals (based on YAML references)
goals = {
    1: "Goal 1: Every time we are asked to turn and talk, I will say one thing on topic to my partner.",
    2: "Goal 2: Every time we talk as a group, I will contribute at least 1 idea or ask 1 question.",
    3: "Goal 3: Raise my hand and make contributions or ask questions.",
    4: "Goal 4: Give my ideas to someone else, then encourage others to share them out.",
    5: "Goal 5: Ask people what they think, and use '3 before me'",
    6: "Goal 6: Ask questions to the teacher or small group when I get lost.",
    7: "Goal 7: Use the phrase, 'I am only 20% sure…' to let people know I am taking a risk.",
    8: "Goal 8: Say something on topic to my shoulder partner, and ask them to share it for me.",
    9: "Goal 9: Stay engaged in on topic conversation with my group members at every opportunity."
}

# Sample emotional/one-word background info
background_samples = [
    "Curious and a little nervous.",
    "Tired but trying to stay focused.",
    "Felt confident today.",
    "Had a tough morning, but ready now.",
    "Looking forward to the weekend.",
    "Distracted by stuff outside of school.",
    "Burned out but still showing up.",
    "Feeling energized.",
    "Focused on the goal.",
    "Meh. Just meh."
]

# Generate dates (past 3 school days, skipping weekend)
today = date.today()
dates = [today - timedelta(days=i) for i in [1, 3, 4]]

# Helper to create rows
def make_entry(student_id, goal_num, date, achievement, outcome, measure, background):
    return {
        "StudentID": student_id,
        "GoalSetDate": date.isoformat(),
        "Goal": goals[goal_num],
        "SuccessMeasures": measure,
        "OutcomeReflection": outcome,
        "GoalAchievement": str(achievement),
        "BackgroundInfo": background
    }

# Data entries
entries = []

# Student 100 - repeats same easy goal, always succeeds
for d in dates:
    entries.append(make_entry(
        100,
        1,
        d,
        4,
        "Said something like always. No big deal.",
        "I’ll know because I say something every time.",
        "Confident and a little bored."
    ))

# Student 200 - tries medium goals, keeps missing
reflections_200 = [
    "I just forgot again.",
    "I wanted to speak but someone else always jumped in.",
    "Felt unsure what to say."
]
for i, d in enumerate(dates):
    entries.append(make_entry(
        200,
        2,
        d,
        random.choice([0, 1, 1]),
        reflections_200[i],
        "I’ll know if I speak during group work.",
        random.choice(background_samples)
    ))

# Student 300 - stretch goals, strong success
goals_300 = [5, 7, 9]
reflections_300 = [
    "More people joined the conversation because I invited them.",
    "Tried the phrase and it helped people listen.",
    "Everyone was into it today—good flow."
]
for i, d in enumerate(dates):
    entries.append(make_entry(
        300,
        goals_300[i],
        d,
        3 + random.randint(0, 1),
        reflections_300[i],
        "Others contribute, and I push my thinking.",
        "Energized, focused, and loving class today." if i == 0 else random.choice(background_samples)
    ))

# Convert to DataFrame
df = pd.DataFrame(entries)

print(df.to_csv(index=False))
