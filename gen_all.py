'''Builds all the assets (cards, wonder stages, and illustrations)
Usage
-----
  python gen_all.py                           # builds all the assets
  python gen_all.py --list                    # list all the ids
'''
import gen_all_illustrations, gen_card, gen_wonders, gen_icons
import sys

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

    print('=== GENERATING CARDS ===')
    gen_card.main(args)

    print('=== GENERATING WONDER STAGES ===')
    gen_wonders.main(args)

    print('=== GENERATING ICONS ===')
    gen_icons.main(args)

if __name__ == '__main__':
    main(sys.argv[1:])
