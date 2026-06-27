import time
from treys import Deck, Evaluator, Card


class PokerGame:
    def __init__(self, room_id, log_callback=None):
        self.room_id = room_id
        self.log_callback = log_callback
        self.players = {}  # sid -> данные игрока
        self.state = "WAITING"  # WAITING, PREFLOP, FLOP, TURN, RIVER, SHOWDOWN, ROUND_END
        self.max_players = 6

        # Настройки стола
        self.small_blind = 50
        self.big_blind = 100

        # Состояние текущей раздачи
        self.pot = 0
        self.current_bet = 0
        self.community_cards = []
        self.community_eval_cards = []
        self.treys_deck = None

        self.dealer_sid = None
        self.current_turn_sid = None
        self.action_queue = []

        # Таймеры
        self.turn_max_time = 15  # Секунд на ход
        self.turn_deadline = 0
        self.round_end_deadline = 0

        self.evaluator = Evaluator()

    # --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

    def _format_card(self, treys_card_int):
        """Конвертирует карту treys (Ah, Td) в формат фронтенда (A♥️, 10♦️)"""
        card_str = Card.int_to_str(treys_card_int)
        rank = card_str[0].upper()
        suit = card_str[1].lower()

        if rank == 'T':
            rank = '10'

        suits_map = {'s': '♠️', 'h': '♥️', 'd': '♦️', 'c': '♣️'}
        return f"{rank}{suits_map.get(suit, '')}"

    def _get_active_players(self):
        """Возвращает игроков, которые сидят за столом (не в лобби)"""
        return [p for p in self.players.values() if p['is_active']]

    def _get_in_hand_players(self):
        """Возвращает игроков, которые еще не сбросили карты в текущей раздаче"""
        return [p for p in self.players.values() if p['is_active'] and not p['folded']]

    def _get_unacted_players(self):
        """Возвращает игроков, которым еще нужно походить (не сбросили и не олл-ин)"""
        return [p for p in self._get_in_hand_players() if not p['all_in']]

    def _deduct_bet(self, sid, amount):
        p = self.players[sid]
        actual_bet = min(p['balance'], amount)
        p['balance'] -= actual_bet
        p['round_bet'] += actual_bet
        self.pot += actual_bet
        return actual_bet

    # --- ИНТЕРФЕЙС СЕРВЕРА ---

    def get_state(self, request_sid=None):
        """Формирует состояние стола для отправки на фронтенд"""
        safe_players = []
        for sid, p in self.players.items():
            # Если идет раздача, чужие карты скрываем
            hide_cards = (sid != request_sid) and (self.state != "SHOWDOWN")

            safe_players.append({
                'sid': sid,
                'tg_id': p['tg_id'],
                'name': p['name'],
                'balance': p['balance'],
                'seat': p['seat'],
                'is_active': p['is_active'],
                'folded': p['folded'],
                'all_in': p['all_in'],
                'round_bet': p['round_bet'],
                'status': p['status'],
                'score_str': p.get('score_str', ''),
                'cards': ['BACK', 'BACK'] if (hide_cards and p['cards']) else p['cards']
            })

        # Для фронтенда нужно передать победителей при шоудауне
        winners = getattr(self, 'last_winners', []) if self.state == "SHOWDOWN" else []

        return {
            'room_id': self.room_id,
            'game_type': 'poker',
            'state': self.state,
            'pot': self.pot,
            'current_bet': self.current_bet,
            'community_cards': self.community_cards,
            'dealer_sid': self.dealer_sid,
            'current_turn_sid': self.current_turn_sid,
            'turn_deadline': self.turn_deadline,
            'turn_max_time': self.turn_max_time,
            'server_time': time.time(),
            'big_blind': self.big_blind,
            'deck_count': len(self.treys_deck.cards) if self.treys_deck else 52,
            'winners': winners,
            'players': safe_players
        }

    def add_player(self, sid, tg_id, name, balance, seat=None):
        if len(self.players) >= self.max_players:
            return False, "Стол заполнен!"

        if seat is None:
            occupied = [p['seat'] for p in self.players.values()]
            for i in range(self.max_players):
                if i not in occupied:
                    seat = i
                    break

        self.players[sid] = {
            'sid': sid,
            'tg_id': tg_id,
            'name': name,
            'balance': balance,
            'seat': seat,
            'cards': [],
            'eval_cards': [],
            'is_active': True,
            'folded': False,
            'all_in': False,
            'round_bet': 0,
            'status': 'В лобби',
            'acted_this_round': False,
            'score_str': ''
        }

        if self.log_callback:
            self.log_callback(self.room_id, f"🤠 {name} сел за стол.")

        if len(self._get_active_players()) >= 2 and self.state in ["WAITING", "ROUND_END"]:
            if self.state == "WAITING":
                self.start_hand()

        return True, "Успешно"

    def remove_player(self, sid):
        if sid in self.players:
            p_name = self.players[sid]['name']
            p_was_active = self.players[sid]['is_active']
            del self.players[sid]

            if self.log_callback:
                self.log_callback(self.room_id, f"🚶 {p_name} покинул стол.")

            # Если игрок вышел во время раздачи
            if self.state not in ["WAITING", "ROUND_END"] and p_was_active:
                if self.current_turn_sid == sid:
                    self.next_turn()
                self._check_early_win()

    def check_timeouts(self):
        """Проверка таймеров каждую секунду (вызывается из server.py)"""
        changed = False
        current_time = time.time()

        if self.state in ["PREFLOP", "FLOP", "TURN", "RIVER"] and self.current_turn_sid:
            if current_time >= self.turn_deadline:
                # Время истекло - автоматический ФОЛД (или ЧЕК, если можно)
                p = self.players.get(self.current_turn_sid)
                if p:
                    to_call = self.current_bet - p['round_bet']
                    if to_call == 0:
                        self.handle_action(self.current_turn_sid, "check")
                    else:
                        self.handle_action(self.current_turn_sid, "fold")
                changed = True

        elif self.state == "ROUND_END":
            if current_time >= self.round_end_deadline:
                self._reset_for_new_hand()
                if len(self._get_active_players()) >= 2:
                    self.start_hand()
                else:
                    self.state = "WAITING"
                changed = True

        return changed

    # --- ИГРОВАЯ ЛОГИКА И УЛИЦЫ ---

    def start_hand(self):
        self.state = "DEALING"
        self.pot = 0
        self.current_bet = self.big_blind
        self.community_cards = []
        self.community_eval_cards = []
        self.treys_deck = Deck()
        self.last_winners = []

        active_players = sorted(self._get_active_players(), key=lambda x: x['seat'])

        # Определение дилера (простое смещение)
        if self.dealer_sid and any(p['sid'] == self.dealer_sid for p in active_players):
            idx = next(i for i, p in enumerate(active_players) if p['sid'] == self.dealer_sid)
            self.dealer_sid = active_players[(idx + 1) % len(active_players)]['sid']
        else:
            self.dealer_sid = active_players[0]['sid']

        dealer_idx = next(i for i, p in enumerate(active_players) if p['sid'] == self.dealer_sid)

        # Раздача карт
        for p in self.players.values():
            p['cards'] = []
            p['eval_cards'] = []
            p['folded'] = True
            p['all_in'] = False
            p['round_bet'] = 0
            p['acted_this_round'] = False
            p['score_str'] = ''

            if p['is_active'] and p['balance'] > 0:
                p['folded'] = False
                p['status'] = 'В игре'
                p['eval_cards'] = self.treys_deck.draw(2)
                p['cards'] = [self._format_card(c) for c in p['eval_cards']]
            else:
                p['status'] = 'Ожидание'

        # Сбор блайндов
        num_players = len(active_players)
        if num_players == 2:
            sb_p = active_players[dealer_idx]
            bb_p = active_players[(dealer_idx + 1) % num_players]
            first_actor = sb_p
        else:
            sb_p = active_players[(dealer_idx + 1) % num_players]
            bb_p = active_players[(dealer_idx + 2) % num_players]
            first_actor = active_players[(dealer_idx + 3) % num_players]

        self._deduct_bet(sb_p['sid'], self.small_blind)
        self._deduct_bet(bb_p['sid'], self.big_blind)

        # Выстраиваем очередь ходов
        start_idx = active_players.index(first_actor)
        self.action_queue = [p['sid'] for p in active_players[start_idx:] + active_players[:start_idx] if
                             not p['folded']]

        if self.log_callback:
            self.log_callback(self.room_id, "🃏 Новая раздача!")

        # Переход к торговле после небольшой паузы на анимацию
        self.state = "PREFLOP"
        self._set_turn(self.action_queue[0])

    def _set_turn(self, sid):
        self.current_turn_sid = sid
        self.turn_deadline = time.time() + self.turn_max_time

    def next_turn(self):
        # Отмечаем, что текущий игрок сделал ход
        if self.current_turn_sid in self.players:
            self.players[self.current_turn_sid]['acted_this_round'] = True

        if self._check_early_win():
            return

        unacted = [p for p in self._get_unacted_players() if
                   not p['acted_this_round'] or p['round_bet'] < self.current_bet]

        if not unacted:
            # Все походили и ставки уравнены -> следующая улица
            self.next_street()
        else:
            # Ищем следующего игрока в очереди
            current_idx = self.action_queue.index(
                self.current_turn_sid) if self.current_turn_sid in self.action_queue else -1

            for i in range(1, len(self.action_queue) + 1):
                next_sid = self.action_queue[(current_idx + i) % len(self.action_queue)]
                p = self.players[next_sid]
                if not p['folded'] and not p['all_in'] and (
                        not p['acted_this_round'] or p['round_bet'] < self.current_bet):
                    self._set_turn(next_sid)
                    return

            # Резервный переход (если что-то пошло не так, идем на след. улицу)
            self.next_street()

    def next_street(self):
        # Сброс флагов хода и ставок раунда
        for p in self.players.values():
            p['acted_this_round'] = False
            p['round_bet'] = 0
            if not p['folded'] and not p['all_in']:
                p['status'] = 'В игре'

        self.current_bet = 0

        # Если активен только 1 или 0 игроков (остальные олл-ин), крутим борд до конца
        unacted = self._get_unacted_players()
        auto_run = len(unacted) <= 1

        if self.state == "PREFLOP":
            self.state = "FLOP"
            drawn = self.treys_deck.draw(3)
            self.community_eval_cards.extend(drawn)
            self.community_cards.extend([self._format_card(c) for c in drawn])
            if self.log_callback: self.log_callback(self.room_id, f"Открыт Флоп: {' '.join(self.community_cards)}")

        elif self.state == "FLOP":
            self.state = "TURN"
            drawn = self.treys_deck.draw(1)
            self.community_eval_cards.append(drawn)
            self.community_cards.append(self._format_card(drawn))
            if self.log_callback: self.log_callback(self.room_id, f"Открыт Терн: {self.community_cards[-1]}")

        elif self.state == "TURN":
            self.state = "RIVER"
            drawn = self.treys_deck.draw(1)
            self.community_eval_cards.append(drawn)
            self.community_cards.append(self._format_card(drawn))
            if self.log_callback: self.log_callback(self.room_id, f"Открыт Ривер: {self.community_cards[-1]}")

        elif self.state == "RIVER":
            self.evaluate_showdown()
            return

        if auto_run:
            # Если торговаться некому, рекурсивно открываем до ривера
            self.next_street()
        else:
            # Передаем ход первому активному после дилера
            active_players = sorted(self._get_in_hand_players(), key=lambda x: x['seat'])
            dealer_idx = next((i for i, p in enumerate(active_players) if p['sid'] == self.dealer_sid), -1)

            self.action_queue = [p['sid'] for p in active_players]

            # Находим первого не олл-ин игрока после дилера
            start_idx = (dealer_idx + 1) % len(active_players)
            for i in range(len(active_players)):
                idx = (start_idx + i) % len(active_players)
                if not active_players[idx]['all_in']:
                    self._set_turn(active_players[idx]['sid'])
                    break

    def handle_action(self, sid, action_type, amount=0):
        if sid != self.current_turn_sid:
            return False, "Сейчас не ваш ход!"

        p = self.players[sid]
        to_call = self.current_bet - p['round_bet']

        if action_type == "fold":
            p['folded'] = True
            p['status'] = 'Пас'
            if self.log_callback: self.log_callback(self.room_id, f"🏳️ {p['name']} ПАС.")

        elif action_type == "check":
            if to_call > 0:
                return False, f"Вы должны уровнять {to_call} или сделать фолд"
            p['status'] = 'Чек'
            if self.log_callback: self.log_callback(self.room_id, f"✊ {p['name']} ЧЕК.")

        elif action_type == "call":
            if p['balance'] <= to_call:
                return self.handle_action(sid, "all_in")
            self._deduct_bet(sid, to_call)
            p['status'] = 'Колл'
            if self.log_callback: self.log_callback(self.room_id, f"💵 {p['name']} КОЛЛ {to_call}.")

        elif action_type == "raise":
            amount = int(amount)
            if amount <= self.current_bet:
                return False, "Сумма повышения мала!"

            total_needed = amount - p['round_bet']
            if p['balance'] <= total_needed:
                return self.handle_action(sid, "all_in")

            self._deduct_bet(sid, total_needed)
            self.current_bet = amount
            p['status'] = 'Рейз'

            # Если был рейз, остальные игроки должны снова ответить
            for other_p in self.players.values():
                if not other_p['folded'] and not other_p['all_in'] and other_p['sid'] != sid:
                    other_p['acted_this_round'] = False

            if self.log_callback: self.log_callback(self.room_id, f"🔥 {p['name']} РЕЙЗ до {amount}.")

        elif action_type == "all_in":
            added = self._deduct_bet(sid, p['balance'])
            if p['round_bet'] > self.current_bet:
                self.current_bet = p['round_bet']
                # Сброс статусов для остальных
                for other_p in self.players.values():
                    if not other_p['folded'] and not other_p['all_in'] and other_p['sid'] != sid:
                        other_p['acted_this_round'] = False

            p['status'] = 'All-In'
            p['all_in'] = True
            if self.log_callback: self.log_callback(self.room_id, f"🚀 {p['name']} идет ALL-IN ({added})!")

        self.next_turn()
        return True, "Действие принято"

    def _check_early_win(self):
        """Проверяет, не остался ли в игре только 1 человек (остальные фолд)"""
        active = self._get_in_hand_players()
        if len(active) == 1:
            winner = active[0]
            self.state = "SHOWDOWN"
            self.current_turn_sid = None
            self.last_winners = [winner['sid']]
            winner['balance'] += self.pot
            winner['score_str'] = "ПОБЕДИТЕЛЬ"
            if self.log_callback:
                self.log_callback(self.room_id, f"🏆 {winner['name']} забирает банк {self.pot} (Все сбросили).")

            self.round_end_deadline = time.time() + 5
            self.state = "ROUND_END"
            return True
        return False

    def evaluate_showdown(self):
        self.state = "SHOWDOWN"
        self.current_turn_sid = None

        active = self._get_in_hand_players()
        if len(active) == 1:
            return self._check_early_win()

        best_score = 9999
        winners = []

        for p in active:
            try:
                # treys: Evaluator.evaluate(board, hand). Меньше = лучше.
                score = self.evaluator.evaluate(self.community_eval_cards, p['eval_cards'])
                hand_class = self.evaluator.get_rank_class(score)
                class_string = self.evaluator.class_to_string(hand_class)

                # Переводим на русский для UI
                ru_classes = {
                    "Straight Flush": "Стрит-Флеш",
                    "Four of a Kind": "Каре",
                    "Full House": "Фулл-Хаус",
                    "Flush": "Флеш",
                    "Straight": "Стрит",
                    "Three of a Kind": "Сет",
                    "Two Pair": "Две Пары",
                    "Pair": "Пара",
                    "High Card": "Старшая Карта"
                }

                p['score_str'] = ru_classes.get(class_string, class_string)
                p['eval_score'] = score

                if score < best_score:
                    best_score = score
                    winners = [p]
                elif score == best_score:
                    winners.append(p)

            except Exception as e:
                print(f"Ошибка вычисления руки: {e}")
                p['score_str'] = "ОШИБКА"

        self.last_winners = [w['sid'] for w in winners]

        # Делим банк
        if winners:
            win_amount = self.pot // len(winners)
            names = []
            for w in winners:
                w['balance'] += win_amount
                names.append(w['name'])

            win_str = winners[0]['score_str']
            if self.log_callback:
                self.log_callback(self.room_id, f"🏆 Победитель: {', '.join(names)} ({win_str})! Выигрыш: {win_amount}")

        self.round_end_deadline = time.time() + 7
        self.state = "ROUND_END"

    def _reset_for_new_hand(self):
        self.pot = 0
        self.current_bet = 0
        self.community_cards = []
        self.community_eval_cards = []
        self.last_winners = []

        # Удаляем игроков без денег
        sids_to_remove = []
        for sid, p in self.players.items():
            if p['balance'] <= 0:
                sids_to_remove.append(sid)

        for sid in sids_to_remove:
            self.remove_player(sid)