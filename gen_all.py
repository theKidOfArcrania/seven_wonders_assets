'''Builds all the assets (cards, wonder stages, and illustrations)
Usage
-----
  python gen_all.py                           # builds all the assets
  python gen_all.py --list                    # list all the ids
'''
import gen_all_illustrations, gen_card, gen_wonders, gen_icons
import os, sys, shutil

def main(argv):
    if argv and argv[0] in ('-h', '--help'):
        print(__doc__)
        return

    do_list = '--list' in argv
    if do_list:
        args = ['--list']
        gen_card.main(args)
        gen_wonders.main(args)
        gen_icons.main(args)
        return

    do_png = '--no-png' not in argv
    args = ['--all']
    if not do_png:
        args.append('--all')

    print('=== GENERATING ILLUSTRATIONS ===')
    gen_all_illustrations.main([])

    print('=== COPYING ILLUSTRATIONS ===')
    if do_png:
        png_dir = os.path.join('out', 'png', 'illustrations')
        os.makedirs(png_dir, exist_ok=True)
        for file in os.listdir('illustration_cache'):
            if not file.endswith('.png') and not file.endswith('segments.json'):
                continue
            path = os.path.join('illustration_cache', file)
            shutil.copy(path, png_dir)

    print('=== GENERATING CARDBACKS ===')
    if do_png:
        build_card_backs()

    print('=== GENERATING CARDS ===')
    gen_card.main(args)

    print('=== GENERATING WONDER STAGES ===')
    gen_wonders.main(args)

    print('=== GENERATING ICONS ===')
    gen_icons.main(args)

def build_card_backs():
    png_dir = os.path.join('out', 'png', 'cards')
    os.makedirs(png_dir, exist_ok=True)
    for age in ['I', 'II', 'III']:
        card_id = f'card_back_age_{age}'
        png_path = os.path.join(png_dir, card_id + '.png')
        svg_str = open(f'{card_id}.svg', 'r').read()
        img = gen_card.render_png(svg_str)
        img.save(png_path)
        print('  %-22s -> %s' % (card_id, png_path))

if __name__ == '__main__':
    main(sys.argv[1:])
