"""
Spoons - A day by day energy aware scheduler

How it works: Schedules one day at a time, using the energy profile of the day to determine when to run tasks. 
It uses a simple greedy algorithm to schedule tasks in the most energy efficient way possible.

Unscheduled tasks are carried over and are promoted after being deffered for too long, based on treshold promotion and ageing.
The algorithm handles tasks with different priorities with hard-deadline checks. This looks at a task and their priority and deadline. Depeneding on the task's priority and deadline, if the task has a more flexible deadline, then it will look at the deadline and push the priority of the task to another day, in overflow. Then, this task will be rechecked the next day to see if it was overdue. If yes, it will flag it and warn the user that the deadline's passed, the carryover let it slip past its deadline.
"""

from dataclasses import dataclass, field

# Global Variables
PROMOTION_THRESHOLD = 1 # promote task one it has been deffered for full day
FULL_DAY_BUDGET = 18 # total energy-cost of a realistic best (energy-5) day, from my own task load. 
                     # This is calculated by assuming on the best day, I can do 18 units of work, which is the sum of the durations of all tasks (3 max effort tasks, and one or two small tasks) I can realistically complete in a day.
BUFFER = 0.85 # only schedule to 85% of the energy capacity to built-in headroom against overcommitment
CAP_FRICTION = 0.75 # no single task can use more than 75% the day's budget
DAY_HOURS = 14 # scheduling time window, 7am - 9pm 

class Task:
    """
    The class Task represents one thing you need to get done. It carries all the data the scheduler needs 
    to decide when to place it in the schedule It considers the name of task, duration of task, the importance or priority, 
    how much energy it demands, and an optional hard deadline, and coutner tracking how many days the task has been rolled 
    over. """
    def __init__(self, name, duration, priority, energy_required, deadline_day=None, days_deferred=0):
        self.name = name
        self.duration = duration  # hours (spends the time budget)
        self.priority = priority  # 1 (low) to 5 (high) importance of the task
        self.energy_required = energy_required  # 1 (low) to 5 (high) energy cost of the task
        self.deadline_day = deadline_day  # day by which the task must be completed
        self.days_deferred = days_deferred  # number of days the task has been deferred


class CheckInEnergy:
    """
    The class CheckInEnergy represents the self-report of the user's energy and motivation levels for the day. 
    It is used to determine how much energy the user has available to complete tasks, and how motivated they are 
    to complete them. The scheduler uses this information to determine the day's energy budget. The two separate variables
    relfect the user's physical energy and mental structure of the Fatigue Assessment Scale (FAS) the algorithm is based on. """
    def __init__(self, energy):
        self.energy = energy  # 1-5 scale

class Block:
    """
    The class Block represents a time slot in the day that the scheduler can put tasks into. It considers the energy 
    the user expects to have during it, how much capacity (work it can hold in the time slot), how much remaining room 
    is still left in the slot of time, and tracks which tasks have been assigned to the schedule so far.
    """
    def __init__(self, label, energy_level):
        self.label = label
        self.energy_level = energy_level 
        self.assigned = []  # list of assigned tasks scheduled in this block

def ask_user_energy_level(prompt, low, high):
    """ Asks user for whole integer between low and high. Reprompts for bad input.
    This is used in the end."""
    while True:
        answer = input(prompt).strip()
        if not answer.isdigit():
            print(f"Please enter a positive whole number between {low} and {high}.")
            continue
        value = int(answer)
        if value < low or value > high:
            print(f"Please enter a positive whole number between {low} and {high}.")
            continue
        return value

def get_tasks_from_user():
    """Ask the user for tasks one at a time and return them as a list of Task
    objects. Each task needs a name, duration, priority, and energy cost. Type
    'done' as the name to finish. """
    tasks = []
    print("\nPlease enter all the tasks you have to do today. Type 'done' for the task name when you're done. :) .\n")
    while True:
        name = input("Task name (or 'done'): ").strip()
        if name.lower() == "done":
            break
        if name == "":
            print("   Please enter a name, or 'done' to finish.")
            continue
        duration = ask_user_energy_level("   Duration in hours (1-14): ", 1, 14)
        priority = ask_user_energy_level("   Priority (1 = low, 5 = high): ", 1, 5)
        energy_required = ask_user_energy_level("   Energy it costs (1 = easy, 5 = draining): ", 1, 5)
        tasks.append(Task(name, duration=duration, priority=priority, energy_required=energy_required))
        print(f"   Added: {name}\n")
    return tasks

def daily_energy_budget(self_reported_energy):
    """
   Convert the 1-5 energy rating into a spendable budget, scaled to a real
    best-day load (18) and reduced to 85% so the plan always leaves headroom.

    Vars: fraction is a number bewteen 0 and 1 representing what portion of a full day's energy you have today.
    Ex:
        - energy = 5: 5/5 = 1.0 (full energy)
        - energy = 3: 3/5 = 0.6 (60% of full energy)
        - energy = 1: 1/5 = 0.2 (20% of full energy)
    @returns: the energy budget for the day, scaled to a full day of work and reduced to 80% for headroom to reduce burnout. 
    """
    fraction = self_reported_energy.energy / 5  # scale to 0-1. """
    return max(0, round(FULL_DAY_BUDGET * fraction * BUFFER))  # scale to 0-18 and reduce to 80% for headroom

def energy_fit(task, block):
    """
    Score how well a task's energy demand matches a block's energy level.
    Returns 5 minus the absolute difference between the two, so a perfect
    match scores 5 and the score drops by 1 for each level of mismatch.
    
    A higher score means a better fit, which steers demanding tasks toward
    higher-energy blocks.
    """
    return 5 - abs(task.energy_required - block.energy_level)  # higher score = better fit, max score is 5

def schedule_one_day(day_index, work_blocks, candidate_tasks, self_reported_energy):
    """Greedily place tasks by priority for one day, spending both the energy
    budget and the time budget. Returns (scheduled, leftover, budget, warnings)."""
    budget = daily_energy_budget(self_reported_energy) # compute today's energy budget
    time_left = DAY_HOURS # how many hours are left in the day to schedule tasks
    spent = 0 # Counts how much energy you've spent so far, starting at 0
    scheduled = [] # list of tasks placed in the schedule for today
    remaining_tasks = list(candidate_tasks) # list of tasks that are still available to be scheduled, starting with all candidate tasks
    deadline_warnings = [] # list collecting overflow tasks passed their deadline. 

    # Deadline check: flag any task that's past its due date and add it to the deadline_warnings list. This is done before scheduling to ensure that any tasks that are overdue are flagged for the user.
    ''' While loop runs O(n) times to schedule one task per pass and removes it from the remaining tasks list.'''
    for task in remaining_tasks:
        if task.deadline_day is not None and task.deadline_day < day_index:
            deadline_warnings.append(task)

    # Ranks promoted tasks (aged past threshold) first, then by priority
    def sort_key(task):
        promoted = 1 # put off by 1 day
        if task.days_deferred >= PROMOTION_THRESHOLD:
            promoted = 2  # promote to higher priority if deferred too long. Max priority is 2, so this will make it the highest priority task to schedule next.
        else: 
            promoted = 0 # normal priority if not promoted 
        return (promoted, task.priority)

    #  Greedy scheduling loop: keep placing until the energy or time runs out, or nothing else fits.
    while budget - spent > 0 and time_left > 0 and remaining_tasks: 
        energy_left = budget - spent # how much energy is left to spend today
        cap = CAP_FRICTION * budget # no single task can use more than half the day's budget

        # A task is eligible if it fits remaining energy AND remaining time,
        # and (unless it's promoted) is under the per-task cap.
        '''Every pass here, it rebuilds and sorts the eligible list. this sorting is in O(nlgn) for comparison based sorting.
        We need to scan 3 blocks n times, but this negs dominated by the O(nlgn) asymptotic time.
        Multiplying the outer loop with the inner loop, we get O(n * nlgn) = O(n^2lgn)'''
        eligable_task = []
        for task in remaining_tasks:
            if task.energy_required > energy_left:
                continue  # can't afford the energy
            if task.duration > time_left:
                continue  # not enough time left
            promoted = task.days_deferred >= PROMOTION_THRESHOLD
            if not promoted and task.energy_required > cap:
                continue  # cap blocks it today (aging will bypass later)
            eligable_task.append(task)

        if not eligable_task:
            break  # no more tasks can fit, so stop scheduling

        # Sort eligable remaining by promotion-then-priority key. Highest priority tasks will be at the front of the list.
        eligable_task.sort(key=sort_key, reverse=True)
        top = eligable_task[0]
        top_group = [task for task in eligable_task if sort_key(task) == sort_key(top)] # gathers all tasks with same tier in priority. 

        #tie breaker for deciding how to schedule tasks of same tier in priority 
        best, best_block, best_score = None, None, -1
        for task in top_group:
            for block in work_blocks:
                    score = task.priority * energy_fit(task, block)
                    if score > best_score:
                        best, best_block, best_score = task, block, score

        # Greedy choice: If we found a task and block that fits the best, assign it. Otherwise, break the loop.
        if best is None:
            break

        # If no valid pair (task and time block that fits together) was found, stop scheduling. 
        best_block.assigned.append(best)
        spent += best.energy_required
        time_left -= best.duration
        scheduled.append(best)
        remaining_tasks.remove(best)

    # Defer whatever remaining leftover tasks to the next day, and increment their days_deferred counter. This is done after scheduling to ensure that any tasks that were not scheduled are carried over to the next day and their deferral count is updated.
    for task in remaining_tasks:
        task.days_deferred += 1
    return scheduled, remaining_tasks, budget, deadline_warnings # hands back your schedule of the day, remaining tasks, energy budget, and any deadline warnings for the day.

def print_daily_schedule(day_name, day_index, work_blocks, scheduled, leftover, budget,
                         self_reported_energy, deadline_warnings):
    """Pretty terminal output: a header, a time-ordered agenda, a priority-ordered
    list, and the backlog. Display only - changes nothing about scheduling."""
    W = 57  # width of the divider lines

    # header box 
    header = f"{day_name}  ·  energy {self_reported_energy.energy}/5  ·  budget {budget} pts  ·  {DAY_HOURS}h"
    print("\n╭" + "─" * W + "╮")
    print("  " + header)
    print("╰" + "─" * W + "╯")

    # --- time-ordered agenda (walk blocks in order) ---
    print("\n  🕐  YOUR SCHEDULE TODAY (in order)")
    print("  " + "─" * (W - 2))
    for block in work_blocks:
        if block.assigned:
            names = ", ".join(task.name for task in block.assigned)
            print(f"   {block.label:<10} [energy {block.energy_level}]   {names}")
        else:
            print(f"   {block.label:<10} [energy {block.energy_level}]   open / rest today")

    # --- priority-ordered list (scheduled is already in priority order) ---
    if scheduled:
        print("\n  ⭐ Sorting tasks by PRIORITY (what mattered most)")
        print("  " + "─" * (W - 2))
        for i, task in enumerate(scheduled, start=1):
            print(f"   {i}. {task.name:<22} (P{task.priority}, energy {task.energy_required})")

    # --- deadline warnings, if any ---
    if deadline_warnings:
        print("\n  ⚠️  Deadline conflicts (missed deadlines)")
        print("  " + "─" * (W - 2))
        for task in deadline_warnings:
            print(f"   • {task.name} is past its due date (day {task.deadline_day})")

    # --- backlog (didn't fit today) ---
    if leftover:
        print("\n  📋  BACKLOG (didn't fit today so carry forward)")
        print("  " + "─" * (W - 2)) # draws a line between headers
        for task in leftover:
            promo = "  ← promoted" if task.days_deferred >= PROMOTION_THRESHOLD else ""
            print(f"   • {task.name:<22} (P{task.priority}, energy {task.energy_required}){promo}")

    print()

if __name__ == "__main__":
    print("\n Welcome to spoons! Enter some tasks and I'll take care of the rest to plan your day ---- \n")

    # Step 1. Daily energy intake checkin (1-5) using the scale, validated.
    energy = ask_user_energy_level(
        "How is your energy levels today? Please give a numeric value from 1 - 5 (1 = exhausted, 5 = fantastic!): ", 
        1, 5
        )
    checkin_today= CheckInEnergy(energy=energy)

    # Step 2. Get tasks from users 
    backlog_of_tasks = get_tasks_from_user() 
    if not backlog_of_tasks:
        print("\nNo more tasks to do! Yay enjoy your freetime. Goodbye!")

    # Hardcoded values based on my own levels on a typical day, regardless of energy.
    else:
        today_time_blocks = [
            Block("Morning", energy_level=5),
            Block("Afternoon", energy_level=4),
            Block("Evening", energy_level=2),
    ]

    # 4. Schedule and print.
    scheduled, leftover, budget, warnings = schedule_one_day(
        0, today_time_blocks, backlog_of_tasks, checkin_today
    )
    print_daily_schedule("Today", 0, today_time_blocks, scheduled, leftover, budget,
                            checkin_today, warnings)

    # Anything still unscheduled after both days.
    if leftover:
        print("Still not scheduled after 2 days:")
        for task in leftover:
            print(f"  - {task.name} (deferred {task.days_deferred}d)")


# runtime is O(n^2 log n)
# Space complexity is O(n)