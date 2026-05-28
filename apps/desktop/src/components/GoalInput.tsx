import { FormEvent, useState } from "react";

type GoalInputProps = {
  onCreateTask: (goal: string) => Promise<void> | void;
};

export function GoalInput({ onCreateTask }: GoalInputProps) {
  const [goal, setGoal] = useState("");
  const [taskError, setTaskError] = useState<string | null>(null);
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
      setTaskError(null);
      setGoal("");
    } catch (error) {
      setTaskError(error instanceof Error ? error.message : "Failed to create task");
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
      {taskError ? (
        <p className="goal-input-error" role="alert">
          {taskError}
        </p>
      ) : null}
      <button type="submit" disabled={isSubmitting}>
        Create task
      </button>
    </form>
  );
}
