import { FormEvent, useState } from "react";

type GoalInputProps = {
  onSubmitGoal: (goal: string) => Promise<void> | void;
  disabled?: boolean;
};

export function GoalInput({ onSubmitGoal, disabled = false }: GoalInputProps) {
  const [goal, setGoal] = useState("");
  const [taskError, setTaskError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const isDisabled = disabled || isSubmitting;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedGoal = goal.trim();

    if (!trimmedGoal || isDisabled) {
      return;
    }

    setIsSubmitting(true);
    try {
      await onSubmitGoal(trimmedGoal);
      setTaskError(null);
      setGoal("");
    } catch (error) {
      setTaskError(error instanceof Error ? error.message : "Failed to plan tasks");
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
        disabled={isDisabled}
        onChange={(event) => setGoal(event.target.value)}
      />
      {taskError ? (
        <p className="goal-input-error" role="alert">
          {taskError}
        </p>
      ) : null}
      <button type="submit" disabled={isDisabled}>
        Plan tasks
      </button>
    </form>
  );
}
