# -*- coding: utf-8 -*-
import re
import json

from core.config import cfg
from core.console import log
from core.cfg_factory import CfgFactory, Variables, VariableTable
from core.locales import locales
from core.utils import error_embed, ok_embed, get

import bot


class QueueChannel:

	cfg_factory = CfgFactory(
		"qc_configs",
		p_key="channel_id",
		variables=[
			Variables.RoleVar(
				"admin_role",
				display="Admin role",
				description="Members with this role will be able to use the bot`s settings and use moderation commands."
			),
			Variables.RoleVar(
				"moderator_role",
				display="Moderator role",
				description="Members with this role will be able to use the bot`s moderation commands."
			),
			Variables.StrVar(
				"prefix",
				display="Command prefix",
				description="Set the prefix before all bot`s commands",
				default="!",
				notnull=True
			),
			Variables.OptionVar(
				"lang",
				display="Language",
				description="Select bot translation language",
				options=locales.keys(),
				default="en",
				notnull=True,
				on_change=bot.update_qc_lang
			),
			Variables.RoleVar(
				"promotion_role",
				display="Promotion role",
				description="Set a role to highlight on !promote and !sub commands.",
			),
			Variables.IntVar(
				"promotion_delay",
				display="Promotion delay",
				description="Set a cooldown time between !promote and !sub commands can be used."
			),
			Variables.BoolVar(
				"rating_calibrate",
				display="Rating calibration boost",
				description="Set to enable rating boost on first 10 user's matches."
			),
			Variables.IntVar(
				"rating_k",
				display="Rating multiplayer",
				description="Change rating K-factor (gain/loss multiplayer)."
			),
			Variables.BoolVar(
				"rating_streaks",
				display="Rating streak boost",
				description="Enable rating streaks (from x1.5 for (3 wins/loses in a row) to x3.0 (6+ wins/loses in a row))."
			)
		],
		tables=[
			VariableTable(
				'ranks', display="Rating ranks",
				variables=[
					Variables.StrVar("rank", default="〈E〉"),
					Variables.IntVar("rating", default=1200),
					Variables.RoleVar("role")
				]
			)
		]
	)

	@classmethod
	async def create(cls, text_channel):
		"""
		This method is used for creating new QueueChannel objects because __init__() cannot call async functions.
		"""

		qc_cfg = await cls.cfg_factory.spawn(text_channel.guild, p_key=text_channel.id)
		self = cls(text_channel, qc_cfg)

		for pq_cfg in await bot.PickupQueue.cfg_factory.select(text_channel.guild, {"channel_id": self.channel.id}):
			self.queues.append(bot.PickupQueue(self, pq_cfg))

		return self

	def __init__(self, text_channel, qc_cfg):
		self.cfg = qc_cfg
		self._ = locales[self.cfg.lang]
		self.queues = []
		self.channel = text_channel
		self.topic = f"> {self._('no players')}"
		self.commands = dict(
			add_pickup=self._add_pickup,
			queues=self._show_queues,
			pickups=self._show_queues,
			add=self._add_member,
			j=self._add_member,
			remove=self._remove_member,
			l=self._remove_member,
			who=self._who,
			set=self._set,
			set_queue=self._set_queue,
			cfg=self._cfg,
			cfg_queue=self._cfg_queue,
			set_cfg=self._set_cfg,
			set_cfg_queue=self._set_cfg_queue
		)

	def update_lang(self):
		self._ = locales[self.cfg.lang]

	def access_level(self, member):
		if (self.cfg.admin_role in member.roles or
					member.id == cfg.DC_OWNER_ID or
					self.channel.permissions_for(member).administrator):
			return 2
		elif self.cfg.moderator_role in member.roles:
			return 1
		else:
			return 0

	async def new_queue(self, name, size, kind):
		kind.validate_name(name)
		if 1 > size > 100:
			raise ValueError("Queue size must be between 2 and 100.")
		if name.lower() in [i.name.lower() for i in self.queues]:
			raise ValueError("Queue with this name already exists.")

		q_obj = await kind.create(self, name, size)
		self.queues.append(q_obj)
		return q_obj

	async def update_topic(self):
		populated = [q for q in self.queues if len(q.queue)]
		if not len(populated):
			new_topic = f"> {self._('no players')}"
		elif len(populated) < 5:
			new_topic = "\n".join([f"> **{q.name}** ({q.status}) | {q.who}" for q in populated])
		else:
			new_topic = "> [" + " | ".join([f"**{q.name}** ({q.status})" for q in populated]) + "]"
		if new_topic != self.topic:
			self.topic = new_topic
			await self.channel.send(self.topic)

	async def process_msg(self, message):
		if not len(message.content) > 1:
			return

		cmd = message.content.split(' ', 1)[0].lower()

		# special commands
		if re.match(r"^\+..", cmd):
			await self._add_member(message, message.content[1:])
		elif re.match(r"^-..", cmd):
			await self._remove_member(message, message.content[1:])
		elif cmd == "++":
			await self._add_member(message, "")
		elif cmd == "--":
			await self._remove_member(message, "")

		# normal commands starting with prefix
		if self.cfg.prefix != cmd[0]:
			return

		f = self.commands.get(cmd[1:])
		if f:
			await f(message, *message.content.split(' ', 1)[1:])

	#  Bot commands #

	async def _add_pickup(self, message, args=""):
		args = args.lower().split(" ")
		if len(args) != 2 or not args[1].isdigit():
			await self.channel.send(embed=error_embed(f"Usage: {self.cfg.prefix}add_pickups __name__ __size__"))
			return
		try:
			pq = await self.new_queue(args[0], int(args[1]), bot.PickupQueue)
		except ValueError as e:
			await self.channel.send(embed=error_embed(str(e)))
		else:
			await self.channel.send(embed=ok_embed(f"[**{pq.name}** ({pq.status})]"))

	async def _show_queues(self, message, args=None):
		if len(self.queues):
			await self.channel.send("> [" + " | ".join(
				[f"**{q.name}** ({q.status})" for q in self.queues]
			) + "]")
		else:
			await self.channel.send("> [ **no queues configured** ]")

	async def _add_member(self, message, args=None):
		targets = args.lower().split(" ") if args else []

		# select the only one queue on the channel
		if not len(targets) and len(self.queues) == 1:
			t_queues = self.queues

		# select queues requested by user
		elif len(targets):
			t_queues = (q for q in self.queues if any(
				(t == q.name or t in (a["alias"] for a in q.cfg.tables.aliases) for t in targets)
			))

		# select active queues or default queues if no active queues
		else:
			t_queues = [q for q in self.queues if len(q.queue)]
			if not len(t_queues):
				t_queues = (q for q in self.queues if q.cfg.is_default)

		for q in t_queues:
			await q.add_member(message.author)
		await self.update_topic()

	async def _remove_member(self, message, args=None):
		targets = args.lower().split(" ") if args else []

		t_queues = (q for q in self.queues if len(q.queue))
		if len(targets):
			t_queues = (q for q in self.queues if any(
				(t == q.name or t in (a["alias"] for a in q.cfg.tables.aliases) for t in targets)
			))
		for q in t_queues:
			await q.remove_member(message.author)
		await self.update_topic()

	async def _who(self, message, args=None):
		targets = args.lower().split(" ") if args else []

		if len(targets):
			t_queues = (q for q in self.queues if any(
				(t == q.name or t in (a["alias"] for a in q.cfg.tables.aliases) for t in targets)
			))
		else:
			t_queues = [q for q in self.queues if len(q.queue)]

		if not len(t_queues):
			await self.channel.send(f"> {self._('no players')}")
		else:
			await self.channel.send("\n".join([f"> **{q.name}** ({q.status}) | {q.who}" for q in t_queues]))

	async def _set(self, message, args=""):
		args = args.lower().split(" ", maxsplit=2)
		if len(args) != 2:
			await self.channel.send(
				embed=error_embed(f"Usage: {self.cfg.prefix}set __variable__ __value__"))
			return
		var_name = args[0].lower()
		if var_name not in self.cfg_factory.variables.keys():
			await self.channel.send(embed=error_embed(f"No such variable '{var_name}'."))
			return
		try:
			await self.cfg.update({var_name: args[1]})
		except Exception as e:
			await self.channel.send(embed=error_embed(str(e)))
		else:
			await self.channel.send(embed=ok_embed(f"Variable __{var_name}__ configured."))

	async def _set_queue(self, message, args=""):
		args = args.lower().split(" ", maxsplit=3)
		if len(args) != 3:
			await self.channel.send(
				embed=error_embed(f"Usage: {self.cfg.prefix}set_queue __queue__ __variable__ __value__"))
			return
		var_name = args[0].lower()
		if var_name not in self.cfg_factory.variables.keys():
			await self.channel.send(embed=error_embed(f"No such variable '{var_name}'."))
			return
		try:
			await self.cfg.update({var_name: args[1]})
		except Exception as e:
			await self.channel.send(embed=error_embed(str(e)))
		else:
			await self.channel.send(embed=ok_embed(f"Variable __{var_name}__ configured."))

	async def _cfg(self, message, args=None):
		await message.author.send(f"```json\n{json.dumps(self.cfg.to_json())}```")

	async def _cfg_queue(self, message, args=None):
		if not args:
			await self.channel.send(embed=error_embed(f"Usage: {self.cfg.prefix}cfg_queue __queue__"))
			return
		args = args.lower()
		for q in self.queues:
			if q.name.lower() == args:
				await message.author.send(f"```json\n{json.dumps(q.cfg.to_json())}```")
				return
		await self.channel.send(embed=error_embed(f"No such queue '{args}'."))

	async def _set_cfg(self, message, args=None):
		if not args:
			await self.channel.send(embed=error_embed(f"Usage: {self.cfg.prefix}set_cfg __json__"))
			return
		try:
			await self.cfg.update(json.loads(args))
		except Exception as e:
			await self.channel.send(embed=error_embed(str(e)))
		else:
			await self.channel.send(embed=ok_embed(f"Channel configuration updated."))

	async def _set_cfg_queue(self, message, args=""):
		args = args.split(" ", maxsplit=1)
		if len(args) != 2:
			await self.channel.send(embed=error_embed(f"Usage: {self.cfg.prefix}set_cfg_queue __queue__ __json__"))
			return
		for q in self.queues:
			if q.name.lower() == args[0].lower():
				try:
					await q.cfg.update(json.loads(args[1]))
				except Exception as e:
					await self.channel.send(embed=error_embed(str(e)))
				else:
					await self.channel.send(embed=ok_embed(f"__{q.name}__ queue configuration updated."))
				return
		await self.channel.send(embed=error_embed(f"No such queue '{args}'."))