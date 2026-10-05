<script lang="ts">
	import { TONE_WORD, type Tone } from './tones';
	/** The 2px line in the state form plus the word (DS §9.8). */
	let { tone, word }: { tone: Tone | 'pass' | 'violet'; word?: string } = $props();
</script>

<span class="state" data-tone={tone}>{word ?? (tone in TONE_WORD ? TONE_WORD[tone as Tone] : tone)}</span>
