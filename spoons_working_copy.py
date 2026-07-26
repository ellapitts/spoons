"""
Spoons - A day by day energy aware scheduler

How it works: Schedules one day at a time, using the energy profile of the day to determine when to run tasks. 
It uses a simple greedy algorithm to schedule tasks in the most energy efficient way possible.

Unscheduled tasks are carried over and are promoted after being deffered for too long, based on treshold promotion and ageing.
The algorithm handles tasks with different priorities with hard-deadline checks. This looks at a task and their priority and deadline. Depeneding on the task's priority and deadline, if the task has a more flexible deadline, then it will look at the deadline and push the priority of the task to another day, in overflow. Then, this task will be rechecked the next day to see if it was overdue. If yes, it will flag it and warn the user that the deadline's passed, the carryover let it slip past its deadline.
"""

from dataclasses import dataclass, field

# Global Variables

"""
Promotion Treshold: Number of days after which a task is promoted to a higher priority / increased in priority. 
Checks 2 days later to see if the task is still pending, if yes, it will be promoted to a higher priority. """
PROMOTION_THRESHOLD = 2 
FULL_DAY_BUDGET = 18 # total energy-cost of a realistic best (energy-5) day, from my own task load. 
# This is calculated by assuming on the best day, I can do 18 units of work, which is the sum of the durations of all tasks (3 max effort tasks, and one or two small tasks) I can realistically complete in a day.
BUFFER = 0.8 # only schedule to 80% of the energy capacity to built-in headroom against overcommitment
CAP_FRICTION = 0.5 # no single task can use more than half the day's budget
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
    def __init__(self, label, energy_level, capacity=1):
        self.label = label
        self.energy_level = energy_level 
        self.assigned = []  # list of assigned tasks scheduled in this block

def daily_energy_budget(self_reported_energy):
    """
   Convert the 1-5 energy rating into a spendable budget, scaled to a real
    best-day load (18) and reduced to 80% so the plan always leaves headroom.

    Vars: fraction is a number bewteen 0 and 1 representing what portion of a full day's energy you have today.
    Ex:
        - energy = 5: 5/5 = 1.0 (full energy)
        - energy = 3: 3/5 = 0.6 (60% of full energy)
        - energy = 1: 1/5 = 0.2 (20% of full energy)
    @returns: the energy budget for the day, scaled to a full day of work and reduced to 80% for headroom to reduce burnout. 
    """
    fraction = self_reported_energy.energy / 5  # scale to 0-1. """
    return max(0, round(FULL_DAY_BUDGET * fraction * BUFFER))  # scale to 0-18 and reduce to 80% for headroom

    # physical_energy = sum(block.remaining for block in work_slots) # how much of my physical capacity should you actually use today
    # scaled_energy = (self_reported_energy / 5) * 0.6 # Energy scale is 1-5 rating based on Fatigue Assessment Scale (FAS); 0.6 is the weight for energy
    # return max(0, round(physical_energy * scaled_energy)) # return the energy budget for the day, whole integer

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

# Method to print the daily schedule in a readable format, showing the day's name, energy levels, budget, and the tasks scheduled in each block. It also shows any leftover tasks that were not scheduled and any deadline warnings for tasks that are past their due date.
def print_daily_schedule(day_name, day_index, work_blocks, scheduled, leftover, budget,
              self_reported_energy, deadline_warnings):
    print("=" * 55)
    print(f"{day_name}:  (check-in: energy {self_reported_energy.energy})")
    print(f"Daily effort budget: {budget} units of energy       | Time window: {DAY_HOURS} hours")
    print("=" * 55)

    # Print the schedule for the day, showing which tasks were assigned to which blocks, and any leftover tasks that were not scheduled.
    for block in work_blocks:
        if block.assigned:
            items = ", ".join(f"{task.name}(P{task.priority}/E{task.energy_required})" for task in block.assigned)
            print(f"  {block.label} [energy {block.energy_level}] -> {items}")
        else:
            print(f"  {block.label} [energy {block.energy_level}] -> (open / rest)")

    # Print any deadline warnings for tasks that were not scheduled and are past their due date.
    if deadline_warnings:
        print("\n  ** DEADLINE CONFLICT:")
        for task in deadline_warnings:
            print(f"     {task.name} is past its due date (day {task.deadline_day})")

    # Print any leftover tasks that were not scheduled, and indicate if they have been promoted due to being deferred too long.
    if leftover:
        print("\n  Rolled to tomorrow:")
        for task in leftover:
            promo = "  <-- PROMOTED (deferred too long)" if task.days_deferred >= PROMOTION_THRESHOLD else ""
            print(f"     {task.name} (deferred {task.days_deferred}d){promo}")
    print()


if __name__ == "__main__":
    backlog_of_tasks = [
        Task("Study for exam",  duration=3, priority=5, energy_required=5),
        Task("Go to class",     duration=2, priority=5, energy_required=3),
        Task("Reply to emails", duration=1, priority=3, energy_required=1),
        Task("Workout",         duration=1, priority=2, energy_required=4),
        Task("Groceries",       duration=1, priority=3, energy_required=2),
    ]

    # One day: today's energy check-in and the day's work blocks. The energy levels of the blocks are set to reflect the user's expected energy throughout the day, with higher energy levels in the morning and lower in the evening.
    today_name = "Today"
    checkin_today = CheckInEnergy(energy=4)
    today_blocks = [
        Block("Morning", energy_level=5),
        Block("Afternoon", energy_level=4),
        Block("Evening", energy_level=2),
    ]

    # generate schedule, overflow, energy budget, warnings.
    scheduled, leftover, budget, warnings = schedule_one_day(0, today_blocks, backlog_of_tasks, checkin_today)

    # print schedule 
    print_daily_schedule(today_name, 0, today_blocks, scheduled, leftover, budget, checkin_today, warnings)

    if leftover:
        print("Still not scheduled by end of window:")
        for task in leftover:
            print(f"  - {task.name} (deferred {task.days_deferred}d). (energy {task.energy_required}, priority {task.priority})")








    # days = []

    # for i, (name, checkin, blocks) in enumerate(days):
    #     scheduled, backlog_of_tasks, budget, warnings = schedule_one_day(i, blocks, backlog_of_tasks, checkin)
    #     print_daily_schedule(name, i, blocks, scheduled, backlog_of_tasks, budget, checkin, warnings)

    # if backlog_of_tasks:
    #     print("Still not scheduled by end of window:")
    #     for task in backlog_of_tasks:
    #         print(f"  - {task.name} (deferred {task.days_deferred}d)")