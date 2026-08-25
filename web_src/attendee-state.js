export function answerStateFromPreview(message) {
  const submitted = message.existingAnswer !== undefined && message.existingAnswer !== null;
  return {
    existingAnswer: submitted ? message.existingAnswer : undefined,
    submitted,
  };
}
