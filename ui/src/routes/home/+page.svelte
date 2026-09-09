<script lang="ts">
	import SlackNotice from '$lib/SlackNotice.svelte';
	import { VIEWS } from '$lib/views';
	const view = VIEWS.find((v) => v.id === 'home')!;
</script>

<SlackNotice {view} />
