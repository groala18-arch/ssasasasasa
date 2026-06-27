import random
import time

SUITS = ['Hearts', 'Diamonds', 'Clubs', 'Spades']
SUITS_RU = {'Hearts': 'Черви ♥️', 'Diamonds': 'Буби ♦️', 'Clubs': 'Крести ♣️', 'Spades': 'Пики ♠️'}


class SvaraGame:
    def __init__(self, room_id, log_callback=None):
        self.room_id = room_id
        self.log_callback = log_callback

        self.players = {}
        self.player_order = []
        self.state = "WAITING_PLAYERS"

        self.pot = 0
        self.current_bet = 0
        self.ante_stake = 50
        self.deck = []
        self.excluded_suit = 'Spades'

        self.current_dealer_sid = None
        self.next_dealer_sid = None

        self.current_player_idx = 0
        self.betting_order = []

        self.dark_bet_active = False
        self.dark_bettor_sid = None
        self.was_dark_last_round = False

        self.raise_count = 0

        self.svara_active = False
        self.svara_pot = 0
        self.svara_buyin_amount = 0
        self.svara_players = []
        self.svara_processed_joiners = set()
        self.svara_eligible_sids = set()

        self.suit_vote_cooldown = 0
        self.suit_vote_yes = 0
        self.suit_vote_no = 0
        self.voted_sids = set()
        self.state_before_vote = "WAITING_PLAYERS"

        self.is_svara_round = False
        self.is_first_game = True
        self.dealer_search_sequence = []

        self.turn_time = 30
        self.turn_deadline = 0
        self.reveal_data = {}
        self.svara_proposer_sid = None

        self.round_players = []

    def log(self, message):
        if self.log_callback:
            self.log_callback(self.room_id, message)
        print(f"[{self.room_id}] [{self.state}] {message}")

    def set_timer(self, seconds=None):
        self.turn_deadline = time.time() + (seconds if seconds else self.turn_time)

    def process_end_of_round(self):
        sids_to_kick = []
        for sid in getattr(self, 'round_players', []):
            if sid in self.players:
                p = self.players[sid]
                if p.get('timeout_this_round', False):
                    p['consecutive_afk_rounds'] = p.get('consecutive_afk_rounds', 0) + 1
                    if p['consecutive_afk_rounds'] >= 2:
                        sids_to_kick.append(sid)
                else:
                    p['consecutive_afk_rounds'] = 0

        for sid in sids_to_kick:
            self.log(f"💤 Игрок {self.players[sid]['name']} кикнут за неактивность (2 кона).")
            self.remove_player(sid)

        self.round_players = []

    def check_timeouts(self):
        if self.state in ["WAITING_PLAYERS", "ROUND_END"]: return False
        if self.turn_deadline == 0 or time.time() < self.turn_deadline: return False

        for sid in self.players:
            req = self.players[sid].get('incoming_zakruzhit')
            if req:
                self.players[sid]['incoming_zakruzhit'] = None
                self.log(f"⏰ Запрос закружить от {req['from_name']} к {self.players[sid]['name']} отменен по таймауту.")

        changed = False

        if self.state == "VOTING_SUIT":
            self.resolve_suit_vote()
            changed = True
        elif self.state == "PRE_ROUND_WAIT":
            self.log("🚀 Время вышло! Начинаем раздачу.")
            self.start_round()
            changed = True
        elif self.state == "CHOOSE_ANTE":
            dealer_sid = self.current_dealer_sid
            if dealer_sid and dealer_sid in self.players:
                self.players[dealer_sid]['timeout_this_round'] = True
                self.log(f"⏱ Время вышло! {self.players[dealer_sid]['name']} автоматически ставит анте 50.")
                self.handle_action(dealer_sid, "set_ante", 50, is_timeout=True)
                changed = True
        elif self.state == "PAYING_ANTE":
            active_sids = [s for s in self.player_order if s in self.players and not self.players[s]['folded']]
            sids_to_kick = []
            for sid in active_sids:
                if not self.players[sid]['acted']:
                    self.players[sid]['timeout_this_round'] = True
                    sids_to_kick.append(sid)
            for sid in sids_to_kick:
                name = self.players[sid]['name']
                self.log(f"🚪 {name} кикнут со стола (не внес анте). Как будете готовы к игре — возвращайтесь!")
                self.remove_player(sid)
                changed = True
        elif self.state == "SVARA_CHECK_IN":
            active_sids = [sid for sid in self.player_order if sid in self.players]
            non_svara = [sid for sid in active_sids if
                         sid not in self.svara_players and sid in self.svara_eligible_sids and not self.players[sid][
                             'folded']]
            for sid in non_svara:
                if self.state != "SVARA_CHECK_IN": break
                if sid not in self.svara_processed_joiners:
                    self.players[sid]['timeout_this_round'] = True
                    self.log(f"⏱ {self.players[sid]['name']} не принял решение по Сваре (Авто-пас).")
                    self.handle_action(sid, "pass_svara", is_timeout=True)
                    changed = True
        elif self.state == "CUT_DECK":
            active_sids = [s for s in self.player_order if s in self.players and not self.players[s]['folded']]
            if active_sids:
                d_idx = active_sids.index(self.current_dealer_sid) if self.current_dealer_sid in active_sids else 0
                cutter_sid = active_sids[(d_idx - 1) % len(active_sids)]
                self.players[cutter_sid]['timeout_this_round'] = True
                self.log(f"✂️ Время вышло! {self.players[cutter_sid]['name']} автоматически постучал.")
                self.handle_action(cutter_sid, "cut", 3, is_timeout=True)
                changed = True
        elif self.state == "EXTRA_HANDS":
            active_sids = [s for s in self.player_order if s in self.players and not self.players[s]['folded']]
            for sid in active_sids:
                if self.state != "EXTRA_HANDS": break
                if self.players[sid]['status'] == 'Выбирает доп. руки':
                    self.players[sid]['timeout_this_round'] = True
                    self.log(f"⏱ Время вышло! {self.players[sid]['name']} играет без докупок.")
                    self.handle_action(sid, "extra_hands", 0, is_timeout=True)
                    changed = True
        elif self.state == "DARK_BETTING":
            active_sids = [s for s in self.player_order if s in self.players and not self.players[s]['folded']]
            if active_sids and self.current_player_idx < len(active_sids):
                curr_sid = active_sids[self.current_player_idx]
                self.players[curr_sid]['timeout_this_round'] = True
                self.log(f"⏱ Время вышло! {self.players[curr_sid]['name']} автоматически переходит в светлую.")
                self.handle_action(curr_sid, "light", is_timeout=True)
                changed = True
        elif self.state == "DEALING":
            self.advance_from_dealing()
            changed = True
        elif self.state == "MAIN_BETTING":
            if self.betting_order and self.current_player_idx < len(self.betting_order):
                curr_sid = self.betting_order[self.current_player_idx]
                p = self.players[curr_sid]
                p['timeout_this_round'] = True
                self.handle_action(curr_sid, "fold", is_timeout=True)
                changed = True
        elif self.state == "WAITING_SVARA_ACCEPT":
            active_sids = [s for s in self.player_order if s in self.players and not self.players[s]['folded']]
            responder_sids = [s for s in active_sids if s != self.svara_proposer_sid]
            if responder_sids:
                responder_sid = responder_sids[0]
                self.players[responder_sid]['timeout_this_round'] = True
                self.log(f"⏱ Время вышло! {self.players[responder_sid]['name']} отказался от ничьей. Вскрытие!")
                self.handle_action(responder_sid, "refuse_svara", is_timeout=True)
                changed = True
        elif self.state == "MUCK_OR_SHOW":
            active_sids = [s for s in self.player_order if s in self.players and not self.players[s]['folded']]
            if active_sids:
                winner_sid = active_sids[0]
                self.players[winner_sid]['timeout_this_round'] = True
                self.handle_action(winner_sid, "muck_cards", is_timeout=True)
                changed = True
        elif self.state == "REVEAL":
            self.finish_reveal()
            changed = True
        elif self.state == "FINDING_DEALER":
            self.state = "CHOOSE_ANTE"
            self.dealer_search_sequence = []
            if self.current_dealer_sid and self.current_dealer_sid in self.players:
                self.log(f"👑 {self.players[self.current_dealer_sid]['name']} получает Туза и становится дилером!")
            self.turn_time = 30
            self.set_timer()
            changed = True

        return changed

    def add_player(self, sid, tg_id, name, balance, seat=None):
        old_sid = None
        for existing_sid, p in list(self.players.items()):
            if p.get('tg_id') == tg_id:
                old_sid = existing_sid
                break

        if old_sid:
            self.log(f"🚨 [БЕЗОПАСНОСТЬ] Обнаружен перезаход (tg_id: {tg_id}). Кикаем старую сессию игрока {name}.")
            self.remove_player(old_sid)

        if len(self.players) >= 9: return False, "Стол заполнен"
        if sid in self.players: return False, "Вы уже за столом"
        if balance < 500: return False, "Для посадки за стол нужно минимум 500 монет!"

        if seat is not None:
            try:
                seat = int(seat)
            except ValueError:
                return False, "Некорректный номер места"
            if seat < 0 or seat >= 9: return False, "Место должно быть от 0 до 8"
            if any(p.get('seat') == seat for p in self.players.values()): return False, "Это место занято"
        else:
            taken_seats = {p.get('seat') for p in self.players.values()}
            for s in range(9):
                if s not in taken_seats:
                    seat = s;
                    break
            else:
                return False, "Нет свободных мест"

        self.players[sid] = {
            'id': sid, 'tg_id': tg_id, 'name': name, 'balance': balance,
            'status': 'В лобби', 'folded': True, 'extra_hands': 0, 'hands': [],
            'original_hands_count': 1, 'round_bet': 0, 'total_round_investment': 0,
            'acted': False, 'score': 0, 'seat': seat,
            'raises_made': 0, 'all_in': False, 'ready': False,
            'zakruzhit_partner': None, 'incoming_zakruzhit': None,
            'consecutive_afk_rounds': 0, 'timeout_this_round': False,
            'last_action': ''
        }

        if sid not in self.player_order: self.player_order.append(sid)
        self.player_order.sort(key=lambda s: self.players[s]['seat'])
        self.log(f"👋 {name} сел за стол (Баланс: {balance} 💰)")
        self.log(f"ℹ️ Учтите, в игре исключена масть: {SUITS_RU[self.excluded_suit]}")
        return True, "Успешно"

    def remove_player(self, sid):
        if sid in self.players:
            name = self.players[sid]['name']
            was_current_turn = False
            state_was = self.state

            if self.state == "MAIN_BETTING" and self.betting_order:
                if self.current_player_idx < len(self.betting_order) and self.betting_order[
                    self.current_player_idx] == sid:
                    was_current_turn = True
            elif self.state == "DARK_BETTING":
                active_sids = [s for s in self.player_order if s in self.players and not self.players[s]['folded']]
                if active_sids and self.current_player_idx < len(active_sids) and active_sids[
                    self.current_player_idx] == sid:
                    was_current_turn = True

            del self.players[sid]
            if sid in self.player_order: self.player_order.remove(sid)
            if sid in self.svara_players: self.svara_players.remove(sid)
            if sid in self.svara_eligible_sids: self.svara_eligible_sids.remove(sid)
            if hasattr(self, 'betting_order') and sid in self.betting_order: self.betting_order.remove(sid)
            if sid in getattr(self, 'round_players', []): self.round_players.remove(sid)

            if state_was != "PAYING_ANTE":
                self.log(f"🏃 {name} покинул стол.")

            if len(self.players) < 2 and self.state not in ["WAITING_PLAYERS", "PRE_ROUND_WAIT"]:
                # Если раунд еще не дошел до раздачи (платим анте и т.д.), возвращаем вложения
                if self.state in ["CHOOSE_ANTE", "PAYING_ANTE", "SVARA_CHECK_IN"]:
                    self.log("⚠️ Недостаточно игроков для продолжения. Возврат фишек.")
                    for s in self.player_order:
                        if s in self.players and self.players[s]['total_round_investment'] > 0:
                            self.players[s]['balance'] += self.players[s]['total_round_investment']
                            self.players[s]['total_round_investment'] = 0
                else:
                    # Если игра уже шла, оставшийся единственный игрок забирает банк
                    remaining_sids = [s for s in self.player_order if s in self.players]
                    if remaining_sids:
                        winner_sid = remaining_sids[0]
                        self.players[winner_sid]['balance'] += self.pot
                        self.log(
                            f"🏆 Все остальные игроки покинули стол. {self.players[winner_sid]['name']} забирает банк {self.pot} 💰.")

                self.reset_game_to_lobby()
                return True

            if state_was == "PAYING_ANTE":
                self.check_paying_ante_progress()
            elif state_was == "EXTRA_HANDS":
                self.check_extra_hands_progress()
            elif state_was == "SVARA_CHECK_IN":
                self.check_svara_join_progress()
            elif state_was == "WAITING_SVARA_ACCEPT":
                self.prepare_reveal()
            elif state_was == "MUCK_OR_SHOW":
                self.finish_reveal()
            elif state_was == "MAIN_BETTING" and was_current_turn:
                if self.betting_order:
                    self.current_player_idx -= 1
                    if self.current_player_idx < 0:
                        self.current_player_idx = len(self.betting_order) - 1
                self.advance_betting_turn()
            elif state_was == "DARK_BETTING" and was_current_turn:
                self.advance_to_extra_hands()
            elif state_was == "PRE_ROUND_WAIT":
                ready_count = sum(1 for s in self.players if self.players[s].get('ready'))
                if ready_count < 2 and self.is_first_game:
                    self.state = "WAITING_PLAYERS"
                    self.turn_deadline = 0
                    self.log("⚠️ Игрок ушел, ожидаем минимум 2-х готовых...")
            return True
        return False

    def reset_game_to_lobby(self):
        self.state = "WAITING_PLAYERS"
        self.pot = 0;
        self.current_bet = 0;
        self.svara_active = False;
        self.svara_pot = 0
        self.svara_buyin_amount = 0;
        self.svara_players = [];
        self.is_first_game = True
        self.dealer_search_sequence = [];
        self.turn_deadline = 0;
        self.reveal_data = {}
        self.svara_proposer_sid = None;
        self.is_svara_round = False
        self.svara_eligible_sids = set();
        self.raise_count = 0
        self.deck = self.generate_deck()

        for p in self.players.values():
            p.update({'status': 'В лобби', 'hands': [], 'original_hands_count': 1, 'score': 0,
                      'raises_made': 0, 'all_in': False, 'ready': False, 'folded': True,
                      'zakruzhit_partner': None, 'incoming_zakruzhit': None, 'last_action': '',
                      'total_round_investment': 0})
        self.log("⚠️ Игра перешла в режим ожидания лобби.")

    def generate_deck(self):
        deck = []
        active_suits = [s for s in SUITS if s != self.excluded_suit]
        extended_ranks = ['6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        for suit in active_suits:
            for rank in extended_ranks: deck.append({'suit': suit, 'rank': rank, 'is_joker': False})
        deck.append({'suit': 'Joker', 'rank': 'Joker', 'is_joker': True})
        return deck

    def translate_card(self, card):
        if card['is_joker']: return "🃏"
        suit_emoji = {'Hearts': '♥️', 'Diamonds': '♦️', 'Clubs': '♣️', 'Spades': '♠️'}
        return f"{card['rank']}{suit_emoji.get(card['suit'], '')}"

    def calculate_hand_score(self, cards):
        if not cards: return 0, 'normal'
        parsed = []
        for c in cards:
            is_joker = c['is_joker']
            rank = c['rank']
            val = 11 if is_joker else (11 if rank == 'A' else (10 if rank in ['J', 'Q', 'K', '10'] else int(rank)))
            parsed.append({'rank': rank, 'suit': c['suit'], 'val': val, 'is_joker': is_joker})

        jokers = [c for c in parsed if c['is_joker']]
        normals = [c for c in parsed if not c['is_joker']]

        is_three_of_a_kind = False
        target_rank = None

        if len(normals) == 3 and normals[0]['rank'] == normals[1]['rank'] == normals[2]['rank']:
            is_three_of_a_kind = True;
            target_rank = normals[0]['rank']
        elif len(normals) == 2 and len(jokers) == 1 and normals[0]['rank'] == normals[1]['rank']:
            is_three_of_a_kind = True;
            target_rank = normals[0]['rank']
        elif len(normals) == 1 and len(jokers) == 2:
            is_three_of_a_kind = True;
            target_rank = normals[0]['rank']

        if is_three_of_a_kind:
            if target_rank == '6': return 33.9, 'three_sixes'
            if target_rank == 'A': return 33.0, 'three_aces'
            return float(normals[0]['val'] * 3), 'three_of_kind'

        sixes = len([c for c in parsed if c['rank'] == '6'])
        aces = len([c for c in parsed if c['rank'] == 'A'])

        max_score = 0
        suits = ['Hearts', 'Diamonds', 'Clubs', 'Spades']

        for s in suits:
            summ = sum(c['val'] for c in normals if c['suit'] == s)
            if not any(c['suit'] == s for c in normals):
                if any(c['rank'] == '6' for c in normals): summ = max(summ, 11.5)
            summ += len(jokers) * 11
            if summ > max_score: max_score = summ

        if sixes == 2 and 22.5 > max_score: return 22.5, 'two_sixes'
        if aces == 2 and 22.0 > max_score: return 22.0, 'two_aces'

        return float(min(31.0, max_score)), 'normal'

    def format_score_display(self, score):
        if score == 33.9: return "33.9 (💥 Три Шестёрки!)"
        if score == 33.0: return "33 (Три Туза)"
        if score == 22.5: return "22.5 (Две Шестёрки)"
        if score == 22.0: return "22 (Два Туза / Свара)"
        if score == 11.5: return "11.5 (Старшая Шестёрка)"
        return str(int(score)) if score.is_integer() else str(score)

    def resolve_suit_vote(self):
        if self.suit_vote_yes > self.suit_vote_no:
            suits = ['Hearts', 'Diamonds', 'Clubs', 'Spades']
            self.excluded_suit = suits[(suits.index(self.excluded_suit) + 1) % len(suits)]
            self.log(f"✅ Голосование прошло! Исключена масть: {SUITS_RU[self.excluded_suit]}")
        else:
            self.log(f"❌ Голосование отклонено. Масть остается прежней.")

        self.suit_vote_cooldown = time.time() + 1800
        self.state = self.state_before_vote
        self.turn_time = 30
        self.set_timer()

    def start_round(self):
        self.deck = self.generate_deck()
        self.raise_count = 0

        active_sids = [sid for sid in self.player_order if sid in self.players and self.players[sid].get('ready')]

        if self.is_first_game:
            if len(active_sids) < 2:
                self.log("⚠️ Менее 2-х игроков готовы. Возврат в лобби.")
                self.reset_game_to_lobby()
                return False, "Недостаточно игроков"
            for sid in self.players:
                if sid not in active_sids: self.players[sid]['folded'] = True
        else:
            for sid in self.player_order:
                if sid in self.players and self.players[sid]['status'] == 'В лобби':
                    self.players[sid]['status'] = 'Ожидание'
                    self.players[sid]['ready'] = True
                    self.players[sid]['folded'] = False
            active_sids = [sid for sid in self.player_order if
                           sid in self.players and self.players[sid]['status'] != 'В лобби']
            if len(active_sids) < 2:
                active_sids = [sid for sid in self.player_order if sid in self.players]
                if len(active_sids) < 2:
                    self.reset_game_to_lobby()
                    return False, "Недостаточно игроков"

        if not self.svara_active:
            self.svara_eligible_sids = set(active_sids)

        if self.svara_active:
            valid_svara_sids = [sid for sid in self.player_order if
                                sid in self.players and sid in self.svara_eligible_sids]
            for sid in self.players:
                if sid not in valid_svara_sids:
                    self.players[sid]['folded'] = True
                    self.players[sid]['status'] = 'Ожидает (Свара)'
            active_sids = valid_svara_sids.copy()

        self.round_players = active_sids.copy()

        for sid in active_sids:
            p = self.players[sid]
            p.update({'folded': False, 'extra_hands': 0, 'hands': [], 'original_hands_count': 1,
                      'round_bet': 0, 'total_round_investment': 0, 'acted': False, 'status': 'Ожидание',
                      'score': 0, 'raises_made': 0, 'all_in': False, 'timeout_this_round': False, 'last_action': ''})
            if not self.is_svara_round:
                p['zakruzhit_partner'] = None;
                p['incoming_zakruzhit'] = None

        self.current_bet = 0;
        self.dark_bet_active = False;
        self.dark_bettor_sid = None
        self.reveal_data = {};
        self.svara_proposer_sid = None
        self.is_svara_round = self.svara_active

        if self.is_first_game and not self.svara_active:
            in_play = [SUITS_RU[s].split()[0] for s in SUITS if s != self.excluded_suit]
            self.log(f"📢 Скинули масть {SUITS_RU[self.excluded_suit]}. В игре {', '.join(in_play)} и Джокер 🃏!")
            self.log("🃏 Ищем дилера! Раздаем карты по кругу до Туза...")
            self.turn_deadline = 0
            temp_deck = self.generate_deck()
            random.shuffle(temp_deck)

            self.dealer_search_sequence = []
            found_ace = False
            while not found_ace:
                for sid in active_sids:
                    if not temp_deck:
                        temp_deck = self.generate_deck();
                        random.shuffle(temp_deck)
                    card = temp_deck.pop(0)
                    self.dealer_search_sequence.append(
                        {'sid': sid, 'card': self.translate_card(card), 'is_ace': card['rank'] == 'A',
                         'seat': self.players[sid]['seat']})
                    if card['rank'] == 'A':
                        self.current_dealer_sid = sid;
                        found_ace = True;
                        break

            self.is_first_game = False;
            self.state = "FINDING_DEALER"
            self.turn_time = len(self.dealer_search_sequence) * 0.4 + 1.5
            self.set_timer()
            return True, "Анимация поиска дилера"

        if self.svara_active:
            self.pot = self.svara_pot;
            self.state = "SVARA_CHECK_IN";
            self.turn_time = 30;
            self.set_timer()
            self.log(f"🔥 НАЧАЛСЯ РАУНД СВАРЫ! Замороженный куш: {self.pot} 💰")
            self.check_svara_join_progress()
        else:
            if getattr(self, 'next_dealer_sid', None) in active_sids:
                self.current_dealer_sid = self.next_dealer_sid
            else:
                self.current_dealer_sid = active_sids[0] if active_sids else None
            self.state = "CHOOSE_ANTE";
            self.turn_time = 30;
            self.set_timer()
            self.log(f"👑 Дилер {self.players[self.current_dealer_sid]['name']} выбирает размер анте...")

        return True, "Раунд запущен"

    def check_paying_ante_progress(self):
        active_sids = [s for s in self.player_order if s in self.players and not self.players[s]['folded']]
        all_paid = all(self.players[sid]['acted'] for sid in active_sids)

        if all_paid:
            if len(active_sids) < 2:
                self.log("⚠️ Менее 2 игроков внесли анте. Возврат фишек и откат в лобби.")
                for s in self.player_order:
                    if s in self.players and self.players[s]['total_round_investment'] > 0:
                        self.players[s]['balance'] += self.players[s]['total_round_investment']
                self.process_end_of_round()
                self.reset_game_to_lobby()
            else:
                self.advance_to_cut_deck()

    def check_svara_join_progress(self):
        active_sids = [sid for sid in self.player_order if sid in self.players and not self.players[sid]['folded']]
        eligible_non_svara = [sid for sid in active_sids if sid not in self.svara_players]

        all_decided = True
        for sid in eligible_non_svara:
            if sid not in self.svara_processed_joiners:
                all_decided = False
                self.players[sid]['status'] = 'Решает по Сваре'

        for sid in self.svara_players:
            if self.players[sid]['balance'] <= 0:
                self.players[sid]['all_in'] = True;
                self.players[sid]['status'] = 'В игре (0 💰)'

        if all_decided:
            self.svara_active = False;
            self.svara_pot = 0
            if len(active_sids) < 2:
                self.log("⚠️ Менее 2 игроков в Сваре. Выдаем банк единственному и откатываемся.")
                if active_sids: self.players[active_sids[0]]['balance'] += self.pot; self.pot = 0
                self.process_end_of_round()
                self.reset_game_to_lobby()
            else:
                self.advance_to_cut_deck()

    def advance_to_cut_deck(self):
        self.state = "CUT_DECK";
        self.turn_time = 30;
        self.set_timer()
        active_sids = [s for s in self.player_order if s in self.players and not self.players[s]['folded']]
        d_idx = active_sids.index(self.current_dealer_sid) if self.current_dealer_sid in active_sids else 0
        cutter_sid = active_sids[(d_idx - 1) % len(active_sids)]
        self.log(f"✂️ Игрок {self.players[cutter_sid]['name']} должен срезать колоду.")

    def execute_cut(self, choice):
        full_deck = self.generate_deck()
        random.shuffle(full_deck)
        if choice == "1":
            self.deck = full_deck[len(full_deck) // 2:] + full_deck[:len(full_deck) // 2]
        elif choice == "2":
            self.deck = full_deck[len(full_deck) // 3:] + full_deck[:len(full_deck) // 3]
        else:
            self.deck = full_deck
        self.advance_to_dark_bet()

    def advance_to_dark_bet(self):
        if self.is_svara_round and not self.was_dark_last_round:
            self.log("ℹ️ В прошлой раздаче не было Темной. В этой Сваре темнить нельзя!")
            self.advance_to_extra_hands()
            return

        self.state = "DARK_BETTING";
        self.turn_time = 30;
        self.set_timer()
        active_sids = [sid for sid in self.player_order if sid in self.players and not self.players[sid]['folded']]
        d_idx = active_sids.index(self.current_dealer_sid) if self.current_dealer_sid in active_sids else -1
        self.current_player_idx = (d_idx + 1) % len(active_sids)
        next_sid = active_sids[self.current_player_idx]

        for sid in active_sids: self.players[sid]['status'] = 'Ожидание'
        self.log(f"🌙 Право тёмной ставки у: {self.players[next_sid]['name']}")

    def advance_to_extra_hands(self):
        if self.is_svara_round:
            self.log("⚠️ В Сваре докупка дополнительных рук отключена!")
            self.deal_cards()
            return

        self.state = "EXTRA_HANDS";
        self.turn_time = 30;
        self.set_timer()
        active_sids = [sid for sid in self.player_order if sid in self.players and not self.players[sid]['folded']]

        for sid in active_sids:
            p = self.players[sid]
            if sid == self.current_dealer_sid or sid == self.dark_bettor_sid:
                p['status'] = 'Докупка запрещена'
            else:
                p['status'] = 'Выбирает доп. руки'

        self.log("🃏 Фаза докупки дополнительных рук.")
        self.check_extra_hands_progress()

    def check_extra_hands_progress(self):
        active_sids = [sid for sid in self.player_order if sid in self.players and not self.players[sid]['folded']]
        still_choosing = [sid for sid in active_sids if self.players[sid]['status'] == 'Выбирает доп. руки']
        if not still_choosing: self.deal_cards()

    def deal_cards(self):
        active_sids = [sid for sid in self.player_order if sid in self.players and not self.players[sid]['folded']]
        d_idx = active_sids.index(self.current_dealer_sid) if self.current_dealer_sid in active_sids else -1
        deal_order = active_sids[d_idx + 1:] + active_sids[:d_idx + 1]

        for sid in active_sids:
            p = self.players[sid]
            count = p.get('original_hands_count', 1)
            p['hands'] = [[] for _ in range(count)]

        for _ in range(3):
            for sid in deal_order:
                for h in self.players[sid]['hands']:
                    if self.deck: h.append(self.deck.pop(0))

        for sid in active_sids:
            p = self.players[sid]
            score, h_type = self.calculate_hand_score(p['hands'][0]) if p['hands'] and p['hands'][0] else (0, 'normal')
            p['score'] = score;
            p['status'] = 'В игре'

        self.state = "DEALING";
        self.turn_time = len(active_sids) * 3 * 0.25 + 1.5;
        self.set_timer()
        self.log(f"🃏 Дилер раздает карты...")

    def advance_from_dealing(self):
        self.init_main_betting()

    def init_main_betting(self):
        self.state = "MAIN_BETTING";
        self.turn_time = 30;
        self.set_timer();
        self.raise_count = 0
        active_sids = [sid for sid in self.player_order if sid in self.players and not self.players[sid]['folded']]

        d_idx = active_sids.index(self.current_dealer_sid) if self.current_dealer_sid in active_sids else -1
        left_1_idx = (d_idx + 1) % len(active_sids)
        left_2_idx = (d_idx + 2) % len(active_sids)

        if self.dark_bet_active:
            if self.dark_bettor_sid == active_sids[left_1_idx]:
                self.betting_order = [active_sids[left_2_idx]]
                idx = (left_2_idx + 1) % len(active_sids)
                while active_sids[idx] != self.dark_bettor_sid:
                    self.betting_order.append(active_sids[idx]);
                    idx = (idx + 1) % len(active_sids)
                self.betting_order.append(self.dark_bettor_sid)
            else:
                self.betting_order = [active_sids[left_1_idx]]
                idx = (left_1_idx + 1) % len(active_sids)
                while active_sids[idx] != active_sids[left_2_idx]:
                    self.betting_order.append(active_sids[idx]);
                    idx = (idx + 1) % len(active_sids)
                self.betting_order.append(active_sids[left_2_idx])
        else:
            self.betting_order = [active_sids[(left_1_idx + i) % len(active_sids)] for i in range(len(active_sids))]

        self.current_player_idx = 0
        for sid in active_sids: self.players[sid]['acted'] = False; self.players[sid]['status'] = 'В игре'

        current_sid = self.betting_order[self.current_player_idx]
        self.log(f"🗣 Ход переходит к: {self.players[current_sid]['name']}. Банк: {self.pot} 💰")

    def advance_betting_turn(self):
        active_in_play = [sid for sid in self.betting_order if sid in self.players and not self.players[sid]['folded']]

        if len(active_in_play) <= 1:
            self.prepare_reveal();
            return

        all_equal_and_acted = True
        for sid in active_in_play:
            p = self.players[sid]
            if p['all_in']: continue
            if p['round_bet'] != self.current_bet or not p['acted']:
                all_equal_and_acted = False;
                break

        if all_equal_and_acted:
            self.prepare_reveal();
            return

        loops = 0
        while loops < len(self.betting_order):
            self.current_player_idx = (self.current_player_idx + 1) % len(self.betting_order)
            next_sid = self.betting_order[self.current_player_idx]
            if next_sid in self.players and not self.players[next_sid]['folded'] and not self.players[next_sid][
                'all_in']:
                break
            loops += 1

        self.set_timer()

    def prepare_reveal(self):
        active_sids = [sid for sid in self.player_order if sid in self.players and not self.players[sid]['folded']]
        self.reveal_data = {'players': [], 'pot': self.pot, 'winners': [], 'is_svara': False, 'svara_buyin': 0,
                            'single_winner': False}
        self.was_dark_last_round = self.dark_bet_active

        if len(active_sids) == 1:
            winner_sid = active_sids[0]
            self.reveal_data['single_winner'] = True
            self.reveal_data['winners'] = [winner_sid]
            self.state = "MUCK_OR_SHOW";
            self.turn_time = 15.0;
            self.set_timer()
            self.current_player_idx = self.betting_order.index(winner_sid) if hasattr(self,
                                                                                      'betting_order') and winner_sid in self.betting_order else 0
            self.log(
                f"🏆 Все спасовали! Ждем решения от {self.players[winner_sid]['name']}: показать карты или сбросить втёмную.")
            return

        self.state = "REVEAL";
        self.turn_time = 2.0;
        self.set_timer()

        player_scores = []
        for sid in active_sids:
            p = self.players[sid]
            hand = p['hands'][0] if p['hands'] else []
            score, h_type = self.calculate_hand_score(hand)
            p['score'] = score;
            p['hands'] = [hand]
            player_scores.append({'sid': sid, 'score': score, 'type': h_type, 'player': p, 'hand': hand})

        has_normal_22 = any(x['score'] == 22.0 and x['type'] not in ['two_sixes', 'two_aces'] for x in player_scores)
        has_higher_than_22 = any(
            x['score'] > 22.0 and x['type'] not in ['two_sixes', 'two_aces'] for x in player_scores)

        for x in player_scores:
            if x['type'] == 'two_sixes':
                if has_normal_22 and not has_higher_than_22:
                    x['score'] = 22.0
                    self.log(f"⚔️ Две шестерки сталкиваются с 22 очками масти — образуется СВАРА!")
                elif has_higher_than_22:
                    x['score'] = 21.9
                    self.log(f"💥 Две шестерки сгорели об старшую масть!")

        player_scores.sort(key=lambda x: x['score'], reverse=True)

        self.log("--- 🃏 РЕЗУЛЬТАТЫ ВСКРЫТИЯ ---")
        for x in player_scores:
            cards_str = " ".join([self.translate_card(c) for c in x['hand']])
            self.log(f"👁️ {x['player']['name']}: {cards_str} ➡️ {self.format_score_display(x['score'])}")
        self.log("------------------------------")

        max_score = player_scores[0]['score']
        winners = [x for x in player_scores if x['score'] == max_score]

        if len(winners) > 1:
            self.reveal_data['is_svara'] = True;
            self.reveal_data['winners'] = [x['sid'] for x in winners]
            num_revealed = len(active_sids)
            divisor = max(2, num_revealed)
            self.reveal_data['svara_buyin'] = self.pot // divisor
            self.log(f"🔥 Будет СВАРА! Вкупка: 1/{divisor} банка.")
        else:
            self.reveal_data['winners'] = [winners[0]['sid']]
            self.log(f"🏆 Победитель: {winners[0]['player']['name']} ({self.format_score_display(max_score)})")

        for x in player_scores:
            is_winner = x['sid'] in self.reveal_data.get('winners', [])
            self.reveal_data['players'].append({
                'sid': x['sid'], 'tg_id': x['player']['tg_id'], 'name': x['player']['name'],
                'cards': [self.translate_card(c) for c in x['hand']],
                'score_num': x['score'], 'score_str': self.format_score_display(x['score']),
                'seat': x['player']['seat'],
                'is_winner': is_winner
            })

    def finish_reveal(self):
        self.process_end_of_round()
        winner_sid = self.reveal_data['winners'][0]
        self.next_dealer_sid = winner_sid

        if self.reveal_data.get('single_winner'):
            self.players[winner_sid]['balance'] += self.pot;
            self.pot = 0
            self.state = "PRE_ROUND_WAIT";
            self.turn_time = 15.0;
            self.set_timer()
            return True, "Победитель забрал банк"

        if self.reveal_data.get('is_svara'):
            self.state = "PRE_ROUND_WAIT";
            self.turn_time = 15.0;
            self.set_timer()
            self.svara_active = True;
            self.svara_pot = self.pot
            self.svara_players = self.reveal_data['winners'];
            self.svara_buyin_amount = self.reveal_data['svara_buyin']
            self.pot = 0
            return True, "Переход к Сваре"

        winner_p = self.players[winner_sid]
        total_won = 0

        if winner_p['all_in']:
            total_round_bets = sum(p['total_round_investment'] for p in self.players.values())
            dead_money = max(0, self.pot - total_round_bets)
            total_won += dead_money;
            self.pot -= dead_money

            for sid in list(self.players.keys()):
                p = self.players[sid]
                claim = min(winner_p['total_round_investment'], p['total_round_investment'])
                total_won += claim;
                self.pot -= claim;
                p['total_round_investment'] -= claim

            if self.pot > 0:
                showdown_sids = [sid for sid in self.player_order if
                                 sid in self.players and not self.players[sid]['folded']]
                if len(showdown_sids) > 0:
                    share = self.pot // len(showdown_sids)
                    for s in showdown_sids: self.players[s]['balance'] += share
                    self.log(f"💬 Остаток банка ({self.pot} 💰) поделен поровну между участниками вскрытия!")
                self.pot = 0
        else:
            total_won = self.pot;
            self.pot = 0

        z_partner_sid = winner_p.get('zakruzhit_partner')
        if z_partner_sid and z_partner_sid in self.players:
            half = total_won // 2
            winner_p['balance'] += (total_won - half)
            self.players[z_partner_sid]['balance'] += half
            self.log(
                f"💬 Игрок {winner_p['name']} делит выигрыш (50/50) со спонсором {self.players[z_partner_sid]['name']}! 🤝")
        else:
            winner_p['balance'] += total_won

        self.state = "PRE_ROUND_WAIT";
        self.turn_time = 15.0;
        self.set_timer()
        return True, "Раунд завершен"

    def handle_action(self, sid, action_type, amount=0, is_timeout=False):
        if sid not in self.players: return False, "Вы не зарегистрированы в игре"
        p = self.players[sid]

        if not is_timeout:
            p['timeout_this_round'] = False
            p['consecutive_afk_rounds'] = 0

        if self.state == "MUCK_OR_SHOW":
            active_sids = [s for s in self.player_order if s in self.players and not self.players[s]['folded']]
            if len(active_sids) == 1 and sid == active_sids[0]:
                if action_type == "show_cards":
                    self.reveal_data['players'] = [{
                        'sid': sid, 'tg_id': p['tg_id'], 'name': p['name'],
                        'cards': [self.translate_card(c) for c in p['hands'][0]],
                        'score_num': p['score'], 'score_str': self.format_score_display(p['score']), 'seat': p['seat'],
                        'is_winner': True
                    }]
                    self.log(f"👀 {p['name']} решил показать свои карты всему столу!")
                    self.state = "REVEAL";
                    self.turn_time = 4.0;
                    self.set_timer()
                    return True, "Карты показаны"
                elif action_type == "muck_cards":
                    self.log(f"🥷 {p['name']} забирает банк, не показывая карты.")
                    self.reveal_data['players'] = [{
                        'sid': sid, 'tg_id': p['tg_id'], 'name': p['name'],
                        'cards': ["BACK"] * len(p['hands'][0]) if p['hands'] else ["BACK", "BACK", "BACK"],
                        'score_num': 0, 'score_str': "СКРЫТО", 'seat': p['seat'], 'is_winner': True
                    }]
                    p['hands'] = [];
                    p['score'] = 0
                    self.state = "REVEAL";
                    self.turn_time = 3.0;
                    self.set_timer()
                    return True, "Карты сброшены"
            return False, "Недоступно"

        if action_type == "propose_suit_change":
            if self.state not in ["WAITING_PLAYERS", "PRE_ROUND_WAIT"]: return False, "Только в лобби"
            if time.time() < self.suit_vote_cooldown: return False, "Голосование доступно раз в 30 минут!"
            self.state_before_vote = self.state;
            self.state = "VOTING_SUIT"
            self.turn_time = 15
            self.set_timer()
            self.suit_vote_yes = 0;
            self.suit_vote_no = 0;
            self.voted_sids = set()
            self.log(f"🗳️ {p['name']} начал голосование за смену масти (Исключена: {SUITS_RU[self.excluded_suit]}).")
            return True, "Голосование начато"

        if action_type == "cast_vote":
            if self.state != "VOTING_SUIT": return False, "Голосование не идет"
            if sid in self.voted_sids: return False, "Вы уже проголосовали"
            self.voted_sids.add(sid)
            if amount == 'yes':
                self.suit_vote_yes += 1
            elif amount == 'no':
                self.suit_vote_no += 1

            active_count = len([s for s in self.player_order if s in self.players])
            if len(self.voted_sids) >= active_count: self.resolve_suit_vote()
            return True, "Голос принят"

        if action_type == "propose_zakruzhit":
            if p['zakruzhit_partner']: return False, "У вас уже есть партнер в этом раунде!"

            parts = str(amount).split('|')
            target_sid = parts[0]
            if target_sid not in self.players: return False, "Игрок не найден"
            target_p = self.players[target_sid]

            if len(parts) > 1 and parts[1].isdigit():
                req_amt = int(parts[1])
            else:
                to_call = self.current_bet - p.get('round_bet', 0)
                shortfall = to_call - p['balance']
                req_amt = shortfall if shortfall > 0 else self.ante_stake

            target_p['incoming_zakruzhit'] = {'from_sid': sid, 'from_name': p['name'], 'amount': req_amt}
            self.turn_deadline += 15
            self.log(f"🤝 {p['name']} просит игрока {target_p['name']} закружить на {req_amt} 💰!")
            return True, "Отправлено"

        if action_type == "answer_zakruzhit":
            req = p.get('incoming_zakruzhit')
            if not req: return False, "Нет запросов"

            if str(amount) == 'accept':
                from_sid = req['from_sid'];
                req_amt = req['amount']
                if from_sid not in self.players: return False, "Игрок уже ушел"
                from_p = self.players[from_sid]

                if p['balance'] < req_amt:
                    self.log(f"❌ У {p['name']} не хватило баланса, чтобы закружить с {req['from_name']}.")
                    p['incoming_zakruzhit'] = None;
                    from_p['incoming_zakruzhit'] = None
                    return False, "Не хватает баланса"

                p['balance'] -= req_amt;
                from_p['balance'] += req_amt
                from_p['zakruzhit_partner'] = sid;
                p['incoming_zakruzhit'] = None
                self.log(f"🔥 {p['name']} согласился закружить с {from_p['name']}, выделив {req_amt} 💰!")
                return True, "Закружили!"
            else:
                from_sid = req['from_sid']
                from_name = req['from_name']
                self.log(f"❌ {p['name']} отказался закружить с {from_name}.")
                if from_sid in self.players: self.players[from_sid]['incoming_zakruzhit'] = None
                p['incoming_zakruzhit'] = None
                return True, "Отказано"

        if self.state in ["WAITING_PLAYERS", "PRE_ROUND_WAIT"]:
            if action_type == "set_ready":
                if p.get('ready'): return False, "Уже готовы"
                p['ready'] = True;
                p['status'] = 'Готов'
                self.log(f"✅ {p['name']} готов к игре!")
                ready_count = sum(1 for s in self.players if self.players[s].get('ready'))
                if self.state == "WAITING_PLAYERS" and ready_count >= 2:
                    self.state = "PRE_ROUND_WAIT";
                    self.turn_time = 15;
                    self.set_timer()
                    self.log("⏳ Отсчет до старта первой игры (15 сек)!")
                return True, "Готов"

        if self.state == "CHOOSE_ANTE":
            if sid != self.current_dealer_sid: return False, "Только дилер выбирает анте"
            if action_type != "set_ante" or amount not in [50, 100, 150]: return False, "Неверная ставка анте"

            self.ante_stake = amount;
            self.pot = amount
            if p['balance'] < amount: return False, "Недостаточно фишек"

            p['balance'] -= amount;
            p['total_round_investment'] = amount
            p['acted'] = True;
            p['status'] = 'Анте внесено'

            active_sids = [s for s in self.player_order if s in self.players and not self.players[s].get('folded')]
            for s in active_sids:
                if s != sid: self.players[s]['status'] = 'Ожидание анте'; self.players[s]['acted'] = False

            self.log(f"👑 Дилер {p['name']} ставит анте {amount} 💰")
            self.state = "PAYING_ANTE";
            self.turn_time = 30;
            self.set_timer()
            self.check_paying_ante_progress()
            return True, "Анте выбрано"

        if self.state == "PAYING_ANTE":
            if p['acted'] or p['folded']: return False, "Уже сделан выбор"
            if action_type == "pay_ante":
                if p['balance'] < self.ante_stake: return False, "Не хватает баланса!"
                p['balance'] -= self.ante_stake;
                self.pot += self.ante_stake
                p['total_round_investment'] += self.ante_stake;
                p['acted'] = True;
                p['status'] = 'В игре'
                p['last_action'] = 'Анте'
                self.log(f"💵 {p['name']} внес анте {self.ante_stake} 💰")
            elif action_type == "fold":
                self.log(f"🚪 {p['name']} отказался вкупаться и покинул стол (Кик).")
                self.remove_player(sid)
                return True, "Кикнут"
            self.check_paying_ante_progress()
            return True, "Выбор принят"

        if self.state == "SVARA_CHECK_IN":
            if sid in self.svara_players: return False, "Вы авто-участник ничьей, платить не нужно"
            if sid in self.svara_processed_joiners: return False, "Вы уже сделали выбор"

            if action_type == "join_svara":
                if p['balance'] < self.svara_buyin_amount: return False, "Не хватает баланса!"
                p['balance'] -= self.svara_buyin_amount;
                self.pot += self.svara_buyin_amount
                p['total_round_investment'] += self.svara_buyin_amount;
                p['status'] = 'В Сваре'
                p['last_action'] = 'Вкупка'
                self.log(f"🔥 {p['name']} входит в Свару ({self.svara_buyin_amount} 💰)")
            else:
                p['folded'] = True;
                p['status'] = 'Пас Свара'
                self.log(f"❌ {p['name']} пасует в Сваре")

            self.svara_processed_joiners.add(sid)
            self.check_svara_join_progress()
            return True, "Выбор принят"

        if self.state == "CUT_DECK":
            active_sids = [s for s in self.player_order if s in self.players and not self.players[s]['folded']]
            d_idx = active_sids.index(self.current_dealer_sid) if self.current_dealer_sid in active_sids else 0
            if sid != active_sids[(d_idx - 1) % len(active_sids)]: return False, "Не ваша очередь резать колоду"
            if action_type != "cut": return False, "Неверное действие"

            cut_names = {"1": "50/50", "2": "верхнюю треть", "3": "постучав"}
            self.log(f"✂️ {p['name']} сдвигает колоду ({cut_names.get(str(amount), '')})")
            self.execute_cut(str(amount))
            return True, "Колода срезана"

        if self.state == "DARK_BETTING":
            active_sids = [s for s in self.player_order if s in self.players and not self.players[s]['folded']]
            if sid != active_sids[self.current_player_idx]: return False, "Не ваш ход"

            if action_type == "dark":
                dark_amount = int(amount)
                if dark_amount not in [50, 100, 150, 200]: return False, "Неверная сумма темной"
                if p['balance'] < dark_amount:
                    action_type = "light"
                else:
                    self.dark_bet_active = True;
                    self.dark_bettor_sid = sid;
                    self.current_bet = dark_amount * 2
                    p['balance'] -= dark_amount;
                    self.pot += dark_amount
                    p['total_round_investment'] += dark_amount;
                    p['round_bet'] = dark_amount * 2
                    p['last_action'] = 'Тёмная'
                    self.log(f"🌙 {p['name']} ТЕМНИТ на {dark_amount} 💰!")
                    self.advance_to_extra_hands()
                    return True, "Тёмная ставка принята"

            if action_type == "light" or action_type == "pass":
                self.log(f"☀️ {p['name']} играет в светлую (отказ темнить).")
                self.dark_offers_count = getattr(self, 'dark_offers_count', 0) + 1
                if self.dark_offers_count < 2 and len(active_sids) > 2:
                    self.current_player_idx = (self.current_player_idx + 1) % len(active_sids)
                    self.set_timer()
                    next_sid = active_sids[self.current_player_idx]
                    self.log(f"🌙 Право тёмной переходит к {self.players[next_sid]['name']}")
                else:
                    self.advance_to_extra_hands()
                return True, "Пас тёмной"

        if self.state == "EXTRA_HANDS":
            if p['status'] != 'Выбирает доп. руки': return False, "Вы не можете сейчас докупать"

            active_sids = [s for s in self.player_order if s in self.players and not self.players[s]['folded']]
            total_extra_hands_bought = sum(self.players[s].get('extra_hands', 0) for s in active_sids)
            cards_left = len(self.deck) - (len(active_sids) * 3) - (total_extra_hands_bought * 3)
            max_global_hands = max(0, cards_left // 3)

            max_affordable = p['balance'] // self.ante_stake
            available = min(max_affordable, max_global_hands, 3)

            req_amount = int(amount)
            if req_amount > available:
                if max_global_hands == 0:
                    return False, "В колоде больше нет карт для докупки!"
                elif max_affordable < req_amount:
                    return False, "Не хватает баланса для покупки стольких рук!"
                else:
                    return False, f"В колоде карт осталось только на {max_global_hands} доп. рук(и)!"

            requested = req_amount
            if requested > 0:
                cost = requested * self.ante_stake
                p['extra_hands'] += requested;
                p['original_hands_count'] += requested
                p['balance'] -= cost;
                self.pot += cost;
                p['total_round_investment'] += cost
                self.log(f"🃏 {p['name']} докупает {requested} доп. рук")
            else:
                self.log(f"🃏 {p['name']} играет без докупок.")

            p['status'] = 'Ожидание раздачи'
            self.check_extra_hands_progress()
            return True, "Докупка завершена"

        if self.state == "WAITING_SVARA_ACCEPT":
            active_sids = [s for s in self.player_order if s in self.players and not self.players[s]['folded']]
            responder_sids = [s for s in active_sids if s != self.svara_proposer_sid]
            if not responder_sids: return False, "Оппонент не найден"
            responder_sid = responder_sids[0]
            if sid != responder_sid: return False, "Ожидаем ответ другого игрока"

            if action_type == "accept_svara":
                self.svara_active = True;
                self.svara_pot = self.pot;
                self.svara_players = active_sids
                self.svara_buyin_amount = self.pot // 2;
                self.pot = 0
                self.state = "PRE_ROUND_WAIT";
                self.turn_time = 15;
                self.set_timer()
                self.log(f"🤝 {p['name']} принимает Свару!")
                return True, "Свара принята"
            elif action_type == "refuse_svara":
                self.log(f"⚔️ {p['name']} отказывается от Свары. Вскрытие!")
                self.prepare_reveal()
                return True, "Свара отклонена"

        if self.state == "MAIN_BETTING":
            current_sid = self.betting_order[self.current_player_idx]
            if sid != current_sid: return False, "Не ваш ход"
            if p['folded'] or p['all_in']:
                self.advance_betting_turn();
                return False, "Вы вне игры"

            to_call = self.current_bet - p['round_bet']
            active_in_play = [s for s in self.betting_order if s in self.players and not self.players[s]['folded']]

            if action_type == "propose_svara":
                if len(active_in_play) != 2: return False, "Предложить Свару можно только 1 на 1"
                if to_call > 0: return False, "Сначала нужно уравнять ставки"
                self.svara_proposer_sid = sid;
                self.state = "WAITING_SVARA_ACCEPT"
                self.turn_time = 30;
                self.set_timer()
                self.log(f"🤝 {p['name']} предлагает Свару")
                return True, "Предложение отправлено"

            if action_type == "fold":
                if len(p['hands']) > 1:
                    p['hands'].pop(0);
                    p['extra_hands'] -= 1;
                    p['round_bet'] = 0;
                    p['acted'] = False
                    p['score'] = self.calculate_hand_score(p['hands'][0])[0] if p['hands'] else 0
                    p['last_action'] = 'Сброс руки'

                    hand_num = p['original_hands_count'] - p['extra_hands']
                    reason = "Время вышло! Автоматический пас." if is_timeout else "сбрасывает руку."
                    self.log(f"🔄 {p['name']} {reason} Переход на {hand_num}-ю руку.")
                    self.set_timer()
                    return True, "Открыта вторая рука"
                else:
                    p['folded'] = True;
                    p['status'] = 'Пас'
                    p['last_action'] = 'Пас'
                    reason = "Время вышло! Автоматический ПАС." if is_timeout else "делает ПАС."
                    self.log(f"🏳️ {p['name']} {reason}")
                    self.advance_betting_turn()
                    return True, "Пас"

            elif action_type == "all_in":
                if to_call <= 0: return False, "У вас хватает фишек, All-In запрещен"
                chips = p['balance']
                p['balance'] = 0;
                self.pot += chips;
                p['total_round_investment'] += chips
                p['round_bet'] += chips;
                p['all_in'] = True;
                p['acted'] = True
                p['last_action'] = 'Ва-Банк'
                self.log(f"🚀 {p['name']} идет ВА-БАНК ({chips} 💰)")
                self.advance_betting_turn()
                return True, "Ва-банк стека"

            elif action_type == "raise":
                if self.current_bet > 0 and self.raise_count >= 3: return False, "Лимит повышений (3) в этом кону достигнут!"
                if self.current_bet >= 1600: return False, "Достигнута максимальная ставка стола (1600)!"

                if sid == self.dark_bettor_sid and p['raises_made'] >= 1 and to_call <= 0:
                    return False, "Тёмный уже сделал финальное повышение! Больше рейзить нельзя."

                raise_amount = int(amount)
                added = raise_amount - p['round_bet']
                is_opening = False

                if self.current_bet == 0:
                    is_opening = True
                    allowed_opens = [50, 100, 150, 200, 250, 300, 350, 400]
                    if raise_amount not in allowed_opens: return False, f"Первая ставка должна быть: {', '.join(map(str, allowed_opens))}"
                    min_raise = raise_amount;
                    max_raise = raise_amount
                else:
                    min_raise = self.current_bet + 50
                    max_raise = min(self.current_bet * 2, 1600)

                if not is_opening:
                    if raise_amount < min_raise: return False, f"Минимальная ставка: {min_raise}"
                    if raise_amount > max_raise: return False, f"Максимальная ставка: {max_raise}"
                    if raise_amount > 1600: return False, "Абсолютный лимит ставки: 1600!"

                if p['balance'] < added: return False, "Недостаточно баланса!"

                p['balance'] -= added;
                self.pot += added;
                p['total_round_investment'] += added
                p['round_bet'] = raise_amount;
                self.current_bet = raise_amount;
                p['acted'] = True
                p['last_action'] = 'Повысил'

                if sid == self.dark_bettor_sid: p['raises_made'] += 1
                if not is_opening: self.raise_count += 1

                for s in self.betting_order:
                    if s != sid: self.players[s]['acted'] = False

                if is_opening:
                    self.log(f"💰 {p['name']} открывает торги ставкой {raise_amount} 💰")
                else:
                    self.log(f"📈 {p['name']} повышает до {raise_amount} 💰")

                self.advance_betting_turn()
                return True, f"Ставка: {raise_amount}"

            elif action_type == "call" or action_type == "reveal":
                if self.current_bet == 0: return False, "Сделайте первую ставку (от 50 до 400) или нажмите ПАС."

                if to_call == 0:
                    if action_type == "reveal" and self.current_bet > 0:
                        p['acted'] = True;
                        p['last_action'] = 'Вскрытие'
                        self.log(f"👁️ {p['name']} вскрывает карты")
                        self.advance_betting_turn()
                        return True, "Вскрытие"
                    return False, "Вы уже уравняли ставки. Сделайте рейз или дождитесь вскрытия."

                if p['balance'] < to_call: return False, "Не хватает баланса. Используйте Ва-Банк."

                p['balance'] -= to_call;
                self.pot += to_call;
                p['total_round_investment'] += to_call
                p['round_bet'] = self.current_bet;
                p['acted'] = True
                p['last_action'] = 'Уравнял'
                self.log(f"✅ {p['name']} уравнивает ставку ({to_call} 💰)")
                self.advance_betting_turn()
                return True, "Ставка принята"

        return False, "Неизвестное действие или фаза игры"

    def get_state(self, request_sid=None):
        players_data = []
        for sid in self.player_order:
            if sid not in self.players: continue
            p = self.players[sid]

            all_cards = []
            for hand in p['hands']: all_cards.extend(hand)

            is_reveal_phase = self.state in ["REVEAL", "PRE_ROUND_WAIT"]

            if self.state in ["CUT_DECK", "DARK_BETTING", "EXTRA_HANDS", "CHOOSE_ANTE", "WAITING_PLAYERS",
                              "SVARA_CHECK_IN", "DEALING", "VOTING_SUIT", "PAYING_ANTE"]:
                cards_display = [""] * len(all_cards);
                score_num = 0;
                score_str = ""
            else:
                if is_reveal_phase or (request_sid and sid == request_sid):
                    if is_reveal_phase and self.reveal_data and sid in self.reveal_data.get('winners', []):
                        winner_data = next((w for w in self.reveal_data.get('players', []) if w['sid'] == sid), None)
                        if winner_data and winner_data.get('score_str') == "СКРЫТО":
                            cards_display = ["BACK"] * len(all_cards);
                            score_num = 0;
                            score_str = "СКРЫТО"
                        else:
                            cards_display = [self.translate_card(c) for c in all_cards]
                            score_num = self.calculate_hand_score(p['hands'][0])[0] if p['hands'] else 0
                            score_str = self.format_score_display(score_num) if p['hands'] else ""
                    else:
                        cards_display = [self.translate_card(c) for c in all_cards]
                        score_num = self.calculate_hand_score(p['hands'][0])[0] if p['hands'] else 0
                        score_str = self.format_score_display(score_num) if p['hands'] else ""
                else:
                    cards_display = ["BACK"] * len(all_cards);
                    score_num = 0;
                    score_str = ""

            inc_data = None
            inc_req = p.get('incoming_zakruzhit')
            if inc_req:
                from_sid = inc_req['from_sid']
                from_p = self.players.get(from_sid)
                is_active = not p['folded'] and p['status'] != 'В лобби'

                if is_active:
                    cards_to_show = ["BACK", "BACK", "BACK"];
                    can_peek = False
                else:
                    cards_to_show = [self.translate_card(c) for c in from_p['hands'][0]] if from_p and from_p.get(
                        'hands') else ["BACK", "BACK", "BACK"]
                    can_peek = True

                inc_data = {'from_sid': inc_req['from_sid'], 'from_name': inc_req['from_name'],
                            'amount': inc_req['amount'], 'cards': cards_to_show, 'can_peek': can_peek}

            partner_cards_display = []
            partner_sid = p.get('zakruzhit_partner')
            if partner_sid and partner_sid in self.players and (p['folded'] or p['status'] == 'В лобби'):
                partner_p = self.players[partner_sid]
                partner_cards_display = [self.translate_card(c) for c in partner_p['hands'][0]] if partner_p.get(
                    'hands') else ["BACK", "BACK", "BACK"]

            players_data.append({
                'sid': sid, 'tg_id': p['tg_id'], 'name': p['name'], 'balance': p['balance'],
                'status': p['status'], 'cards': cards_display, 'score_num': score_num, 'score_str': score_str,
                'round_bet': p['round_bet'], 'extra_hands_count': len(p['hands']),
                'original_hands_count': p.get('original_hands_count', 1), 'seat': p['seat'],
                'raises_made': p['raises_made'], 'is_dark_bettor': (sid == self.dark_bettor_sid),
                'folded': p['folded'], 'all_in': p['all_in'], 'ready': p.get('ready', False),
                'incoming_zakruzhit': inc_data, 'zakruzhit_partner': partner_sid,
                'partner_cards': partner_cards_display,
                'last_action': p.get('last_action', '')
            })

        current_turn_sid = None
        active_sids = [s for s in self.player_order if s in self.players and not self.players[s]['folded']]

        if self.state == "CHOOSE_ANTE":
            if self.current_dealer_sid in self.players: current_turn_sid = self.current_dealer_sid
        elif self.state == "CUT_DECK":
            if active_sids:
                d_idx = active_sids.index(self.current_dealer_sid) if self.current_dealer_sid in active_sids else 0
                current_turn_sid = active_sids[(d_idx - 1) % len(active_sids)]
        elif self.state == "DARK_BETTING":
            if active_sids and self.current_player_idx < len(active_sids): current_turn_sid = active_sids[
                self.current_player_idx]
        elif self.state == "MAIN_BETTING":
            if self.betting_order and self.current_player_idx < len(self.betting_order): current_turn_sid = \
                self.betting_order[self.current_player_idx]
        elif self.state == "MUCK_OR_SHOW":
            if active_sids: current_turn_sid = active_sids[0]

        dark_raised = False
        if self.dark_bettor_sid and self.dark_bettor_sid in self.players:
            dark_raised = self.players[self.dark_bettor_sid].get('raises_made', 0) >= 1

        return {
            "state": self.state, "pot": self.pot, "current_bet": self.current_bet, "ante_stake": self.ante_stake,
            "dealer_sid": self.current_dealer_sid, "current_turn_sid": current_turn_sid,
            "players": players_data, "svara_active": self.svara_active,
            "svara_buyin_amount": self.svara_buyin_amount, "turn_deadline": self.turn_deadline,
            "turn_max_time": self.turn_time, "server_time": time.time(), "is_first_game": self.is_first_game,
            "dealer_search_sequence": getattr(self, 'dealer_search_sequence',
                                              []) if self.state == "FINDING_DEALER" else [],
            "reveal_data": getattr(self, 'reveal_data', {}) if self.state in ["REVEAL", "PRE_ROUND_WAIT"] else {},
            "svara_proposer_sid": getattr(self, 'svara_proposer_sid', None), "dark_raised": dark_raised,
            "excluded_suit": self.excluded_suit, "deck_count": len(self.deck),
            "raise_count": getattr(self, 'raise_count', 0)
        }