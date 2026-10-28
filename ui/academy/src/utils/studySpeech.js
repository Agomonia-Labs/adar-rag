export function studyArtifactSpeechText(artifact) {
  const content = String(artifact?.content || '').trim();
  if (!content) return '';
  try {
    const parsed = JSON.parse(content);
    if (artifact.artifact_type === 'flashcards') {
      const cards = Array.isArray(parsed) ? parsed : parsed.cards;
      if (Array.isArray(cards)) {
        return cards.map((card, index) => (
          `Flashcard ${index + 1}. Question: ${card.front || card.question || ''}. Answer: ${card.back || card.answer || ''}.`
        )).join('\n\n');
      }
    }
    if (artifact.artifact_type === 'practice_questions' && Array.isArray(parsed.questions)) {
      const questions = parsed.questions.map((question, index) => {
        const options = Array.isArray(question.options)
          ? question.options.map(option => `${option.id || ''}. ${option.text || ''}`).join('. ')
          : '';
        return `Question ${index + 1}. ${question.question || ''}${options ? `. Options: ${options}` : ''}`;
      });
      return [parsed.instructions || '', ...questions].filter(Boolean).join('\n\n');
    }
  } catch {
    // Narrative artifacts are Markdown, not JSON. The backend removes their
    // presentation syntax before synthesis.
  }
  return content;
}
