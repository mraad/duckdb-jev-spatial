"""Trial only: ask larger models the capital question that Laya answers in native/capitals-example.sql.

Not a Jev backend. The Claude run sends city and country names to the Anthropic API.
Local run: start `llama-server --model <gguf> --port 8089` first.
Claude run: `uv run --with anthropic scripts/try_larger_models.py --claude ...` with credentials in the environment.
Azure run: `--azure` uses AZURE_API_BASE, AZURE_API_KEY and AZURE_API_DEPLOYMENT (v1 URL shape, `api-key` header).
"""
import argparse
import json
import os
from pathlib import Path
import re
import urllib.request

QUESTION = 'Is this city the capital of its country?'
PROMPT = 'City: {city}\nCountry: {country}\n' + QUESTION + ' Answer with exactly one word: yes or no.'


def parse(text):
    words = re.findall(r'[a-z]+', re.sub(r'<think>.*?</think>', '', text, flags=re.S).lower())
    if not words or words[0] not in ('yes', 'no'):
        raise ValueError(f'Unexpected answer: {text!r}')
    return words[0] == 'yes'


def ask_local(url, city, country):
    body = {'messages': [{'role': 'user', 'content': PROMPT.format(city=city, country=country) + ' /no_think'}],
            'temperature': 0, 'max_tokens': 512}
    request = urllib.request.Request(url + '/v1/chat/completions', json.dumps(body).encode(),
                                     {'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)['choices'][0]['message']['content']


def ask_azure(base, key, deployment, city, country):
    body = {'model': deployment, 'messages': [{'role': 'user', 'content': PROMPT.format(city=city, country=country)}]}
    request = urllib.request.Request(base.rstrip('/') + '/openai/v1/chat/completions', json.dumps(body).encode(),
                                     {'Content-Type': 'application/json', 'api-key': key})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)['choices'][0]['message']['content']


def ask_claude(client, model, city, country):
    response = client.messages.create(
        model=model, max_tokens=2048, output_config={'effort': 'low'},
        messages=[{'role': 'user', 'content': PROMPT.format(city=city, country=country)}])
    if response.stop_reason != 'end_turn':
        raise ValueError(f'Stopped with {response.stop_reason}')
    return next(block.text for block in response.content if block.type == 'text')


def score(rows, ask):
    answers = []
    for city, country, truth in rows:
        raw = ask(city, country)
        answers.append({'city': city, 'country': country, 'expected': bool(truth),
                        'answer': parse(raw), 'raw': raw.strip()[-40:]})
    wrong = [a['city'] for a in answers if a['answer'] != a['expected']]
    return {'correct': len(answers) - len(wrong), 'total': len(answers), 'wrong': wrong, 'answers': answers}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cities', type=Path, default=Path('artifacts/capitals-example/rewording-noul.json'))
    parser.add_argument('--local-url', help='llama-server base URL, for example http://127.0.0.1:8089')
    parser.add_argument('--local-name', default='local model')
    parser.add_argument('--claude', help='Claude model ID, for example claude-opus-5-5')
    parser.add_argument('--azure', action='store_true', help='Use the Azure OpenAI deployment named in the environment')
    parser.add_argument('--merge', action='store_true', help='Keep the models already in the output file')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cities = json.loads(args.cities.read_text())
    sets = {'example_cities': cities['dev'], 'other_cities': cities['held']}
    report = {'question': QUESTION, 'prompt': PROMPT, 'models': {}}
    if args.merge and args.output.is_file():
        report['models'] = json.loads(args.output.read_text())['models']
    if args.azure:
        base, key, deployment = (os.environ[k] for k in ('AZURE_API_BASE', 'AZURE_API_KEY', 'AZURE_API_DEPLOYMENT'))
        report['models'][deployment + ' (Azure OpenAI gateway)'] = {
            name: score(rows, lambda c, k: ask_azure(base, key, deployment, c, k)) for name, rows in sets.items()}
    if args.local_url:
        report['models'][args.local_name] = {
            name: score(rows, lambda c, k: ask_local(args.local_url, c, k)) for name, rows in sets.items()}
    if args.claude:
        import anthropic
        client = anthropic.Anthropic()
        report['models'][args.claude] = {
            name: score(rows, lambda c, k: ask_claude(client, args.claude, c, k)) for name, rows in sets.items()}
    args.output.write_text(json.dumps(report, indent=1, ensure_ascii=False) + '\n')
    for model, result in report['models'].items():
        for name, r in result.items():
            print(f"{model} | {name}: {r['correct']}/{r['total']} wrong={r['wrong']}")


if __name__ == '__main__':
    main()
