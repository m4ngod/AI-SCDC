import { FormEvent, useState } from "react";

type GoalInputProps = {
  onCreateTask: (goal: string) => Promise<void> | void;
};

export function GoalInput({ onCreateTask }: GoalInputProps) {
  const [goal, setGoal] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedGoal = goal.trim();

    if (!trimmedGoal || isSubmitting) {
      return;
    }

    setIsSubmitting(true);
    try {
      await onCreateTask(trimmedGoal);
      setGoal("");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <form className="goal-input" onSubmit={handleSubmit}>
      <label htmlFor="goal">Goal</label>
      <textarea
        id="goal"
        name="goal"
        rows={4}
        value={goal}
        onChange={(event) => setGoal(event.target.value)}
      />
      <button type="submit" disabled={isSubmitting}>
        Create task
      </button>
    </form>
  );
}
