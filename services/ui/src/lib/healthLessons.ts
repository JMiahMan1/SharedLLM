/**
 * The micro-lesson library (Health hub Phase 6): one short card a day.
 *
 * Reviewed, plain-language content written for the whole family. Every card is
 * fine for a child: nothing about calories, weight or dieting, no food is
 * "bad", and nothing here is medical advice. Each card ends with one small
 * thing to try today. The card of the day rotates through the library by date,
 * so every member sees the same card on the same day and can talk about it.
 */

export type LessonTopic = 'nutrition' | 'sleep' | 'movement' | 'stress' | 'water';

export interface Lesson {
  id: string;
  topic: LessonTopic;
  title: string;
  body: string;
  /** One small, concrete thing to try today. */
  tryToday: string;
}

export const TOPIC_LABELS: Record<LessonTopic, string> = {
  nutrition: 'Food',
  sleep: 'Sleep',
  movement: 'Movement',
  stress: 'Calm',
  water: 'Water',
};

export const LESSONS: Lesson[] = [
  {
    id: 'plate-half-plants',
    topic: 'nutrition',
    title: 'Half a plate of plants',
    body: 'Vegetables and fruit bring fibre, vitamins and water with every bite. Filling about half the plate with them leaves plenty of room for the rest of the meal.',
    tryToday: 'Add one vegetable or fruit to a meal you were already going to eat.',
  },
  {
    id: 'protein-every-meal',
    topic: 'nutrition',
    title: 'A little protein at each meal',
    body: 'Eggs, beans, yoghurt, fish, chicken, nuts and tofu help muscles grow and repair, and they keep you full for longer. Spreading them across the day works better than one big serving.',
    tryToday: 'Check that breakfast has some protein in it.',
  },
  {
    id: 'eat-the-rainbow',
    topic: 'nutrition',
    title: 'Eat the rainbow',
    body: 'Different coloured fruits and vegetables carry different nutrients. Red tomatoes, orange carrots, green peas and purple berries each bring something the others do not.',
    tryToday: 'Count the colours on your plate at dinner and try to add one more.',
  },
  {
    id: 'slow-down',
    topic: 'nutrition',
    title: 'Slow down and notice',
    body: 'It takes a little while for your body to tell you it is full. Eating slowly, putting the fork down between bites and talking at the table give it time to catch up.',
    tryToday: 'Eat one meal today without a screen at the table.',
  },
  {
    id: 'whole-grains',
    topic: 'nutrition',
    title: 'Choose the whole grain',
    body: 'Whole-grain bread, oats, brown rice and whole-wheat pasta keep the outer layers of the grain, where most of the fibre is. Fibre helps digestion and steady energy.',
    tryToday: 'Swap one white grain for a whole-grain version.',
  },
  {
    id: 'snack-pairs',
    topic: 'nutrition',
    title: 'Snacks that come in pairs',
    body: 'A snack that pairs a fruit or vegetable with some protein, like apple and peanut butter or carrots and hummus, lasts longer than either on its own.',
    tryToday: 'Plan tomorrow’s snack as a pair.',
  },
  {
    id: 'cook-together',
    topic: 'nutrition',
    title: 'Cook it together',
    body: 'People who help make a meal are more willing to try it. Washing, stirring and measuring are good jobs for younger cooks; older ones can follow a whole recipe.',
    tryToday: 'Give someone a job in tonight’s dinner.',
  },
  {
    id: 'sleep-same-time',
    topic: 'sleep',
    title: 'Same time, every day',
    body: 'Your body keeps an inner clock. Going to bed and getting up at about the same time, weekends included, makes falling asleep and waking up easier.',
    tryToday: 'Pick a bedtime for tonight and set a reminder 30 minutes before it.',
  },
  {
    id: 'sleep-screens',
    topic: 'sleep',
    title: 'Screens off before bed',
    body: 'Bright screens and exciting videos tell your brain it is still daytime. Putting the phone away a while before bed helps sleep come sooner.',
    tryToday: 'Charge your phone outside the bedroom tonight.',
  },
  {
    id: 'sleep-morning-light',
    topic: 'sleep',
    title: 'Morning light',
    body: 'Daylight soon after waking helps set your inner clock, which makes it easier to feel sleepy at night. A few minutes outside counts, even on a cloudy day.',
    tryToday: 'Step outside within an hour of getting up.',
  },
  {
    id: 'sleep-wind-down',
    topic: 'sleep',
    title: 'A wind-down routine',
    body: 'The same calm steps each night, like a shower, reading or prayer, signal to your body that sleep is coming. A cool, dark, quiet room helps too.',
    tryToday: 'Choose two calm things to do in the half hour before bed.',
  },
  {
    id: 'sleep-how-much',
    topic: 'sleep',
    title: 'How much sleep?',
    body: 'Children need more sleep than adults: school-age children about 9 to 12 hours, teenagers about 8 to 10, and most adults at least 7. Feeling rested during the day is a good sign.',
    tryToday: 'Work back from your wake-up time to find tonight’s bedtime.',
  },
  {
    id: 'move-every-hour',
    topic: 'movement',
    title: 'Get up every hour',
    body: 'Sitting still for a long time makes the body stiff and the mind foggy. A two-minute walk, a stretch or a trip for water breaks it up.',
    tryToday: 'Stand up and move for two minutes every hour this afternoon.',
  },
  {
    id: 'move-brisk',
    topic: 'movement',
    title: 'What brisk means',
    body: 'Brisk walking is when you can still talk but would rather not sing. Adults do well with about 150 minutes of that a week; children and teens with about an hour of play a day.',
    tryToday: 'Take a ten-minute brisk walk.',
  },
  {
    id: 'move-play',
    topic: 'movement',
    title: 'Play counts',
    body: 'Movement does not have to be a workout. Tag, dancing in the kitchen, biking to a friend’s house and gardening all count.',
    tryToday: 'Do something active together as a family for 15 minutes.',
  },
  {
    id: 'move-strong',
    topic: 'movement',
    title: 'Stay strong',
    body: 'Muscles and bones get stronger when they work against something: climbing, carrying, push-ups or squats. A couple of times a week is enough to make a difference.',
    tryToday: 'Do ten squats while the kettle boils or the microwave runs.',
  },
  {
    id: 'move-small-start',
    topic: 'movement',
    title: 'Start smaller than you think',
    body: 'A habit that is easy to keep beats a big plan that stops after a week. Five minutes every day builds more than one long session now and then.',
    tryToday: 'Pick a five-minute movement you could do every day this week.',
  },
  {
    id: 'calm-breathe',
    topic: 'stress',
    title: 'Breathe out longer',
    body: 'When you feel upset, breathing out slowly tells your body it is safe. Try breathing in for four counts and out for six, a few times in a row.',
    tryToday: 'Take five slow breaths before you start your next task.',
  },
  {
    id: 'calm-name-it',
    topic: 'stress',
    title: 'Name the feeling',
    body: 'Saying what you feel, like "I am worried" or "I am frustrated", makes it smaller and easier to handle. Telling someone you trust helps even more.',
    tryToday: 'Tell someone at home one thing that was hard today and one that was good.',
  },
  {
    id: 'calm-gratitude',
    topic: 'stress',
    title: 'Three good things',
    body: 'Noticing what went well, even small things, helps people feel calmer and more hopeful. Many families do this at dinner or bedtime.',
    tryToday: 'Write down or say three good things from today.',
  },
  {
    id: 'calm-outside',
    topic: 'stress',
    title: 'Time outside',
    body: 'Time in green spaces, a park, a garden or under a tree, helps people feel calmer. It also tends to come with movement and daylight.',
    tryToday: 'Spend ten minutes outside without your phone.',
  },
  {
    id: 'water-first',
    topic: 'water',
    title: 'Water first',
    body: 'Water is the best drink for thirst. Keeping a filled bottle where you can see it makes it easy to sip through the day.',
    tryToday: 'Fill a water bottle this morning and keep it with you.',
  },
  {
    id: 'water-with-meals',
    topic: 'water',
    title: 'A glass with every meal',
    body: 'Linking a habit to one you already have makes it stick. A glass of water at each meal adds up without needing to think about it.',
    tryToday: 'Put a glass of water at every place when the table is set.',
  },
  {
    id: 'water-signs',
    topic: 'water',
    title: 'Listen to thirst',
    body: 'Feeling thirsty, tired or getting a headache can mean you need a drink, especially on hot days or after playing hard. Drink a little before, during and after being active.',
    tryToday: 'Take a drink of water before and after any exercise today.',
  },
];

/** Days since 1 January 1970 for a local `YYYY-MM-DD`. */
function dayNumber(day: string): number {
  const [year, month, date] = day.split('-').map(Number);
  return Math.floor(Date.UTC(year, month - 1, date) / 86_400_000);
}

/** The index of the card of the day: the same for every member on the same day. */
export function lessonIndexFor(day: string, count = LESSONS.length): number {
  return ((dayNumber(day) % count) + count) % count;
}
