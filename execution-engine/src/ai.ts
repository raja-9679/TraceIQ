import OpenAI from 'openai';

const openai = new OpenAI({
    apiKey: process.env.OPENAI_API_KEY,
});

export class AIEngine {
    async healSelector(brokenSelector: string, domSnapshot: string): Promise<string> {
        if (!process.env.OPENAI_API_KEY) return "";

        try {
            const response = await openai.chat.completions.create({
                model: "gpt-4o",
                messages: [
                    {
                        role: "user",
                        content: `
                        The selector '${brokenSelector}' failed to find an element.
                        Here is the DOM snapshot:
                        ${domSnapshot}
                        
                        Find the element that most likely corresponds to the broken selector.
                        Return ONLY the new selector.
                        `
                    }
                ]
            });
            return response.choices[0].message.content?.trim() || "";
        } catch (e) {
            console.error("AI Healing failed:", e);
            return "";
        }
    }
}
