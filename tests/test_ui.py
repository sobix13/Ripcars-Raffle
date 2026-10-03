import copy
import json
import re
from unittest.mock import AsyncMock

import discord

from ripcars_raffle.admin_ui import Confirm,DraftDetails,DraftJSON,DraftRoles,Hub,SettingEditor,Settings,Setup
from ripcars_raffle.public_ui import RaffleButton,panel_embed,public_view
from tests.fakes import AsyncCase,interaction


class UITests(AsyncCase):
    async def test_setup_defaults_retained(self):
        view=Setup(self.bot,9001,self.cfg,1)
        self.assertEqual([x.id for x in view.children[0].default_values],[10])
        self.assertEqual([x.id for x in view.children[1].default_values],[30])
        self.assertEqual([x.id for x in view.children[2].default_values],[100])
    async def test_channel_selection_survives_form_refresh(self):
        view=Setup(self.bot,9001,self.cfg,1);menu=view.children[2];menu._values=[self.guild.log]
        i=interaction(self.bot,self.guild);await menu.callback(i)
        refreshed=i.response.edit_message.call_args.kwargs["view"]
        self.assertEqual([x.id for x in refreshed.children[2].default_values],[101])
    async def test_draft_role_defaults_retained(self):
        rid=await self.draft(rules={"any_roles":[11],"all_roles":[12]});r=await self.bot.db.get(1,rid)
        view=DraftRoles(self.bot,9001,r)
        self.assertEqual([x.id for x in view.children[0].default_values],[11]);self.assertEqual([x.id for x in view.children[1].default_values],[12])
    async def test_panel_branching_stays_under_discord_limits(self):
        view=Hub(self.bot,9001);options=view.children[0].options
        self.assertLessEqual(len(options),25);self.assertIn("policy",[x.value for x in options])
    async def test_brand_settings_do_not_mix_role_policy(self):
        view=Settings(self.bot,9001,self.cfg,1,("brand","bot_name","website","color","texts."))
        self.assertTrue(all(o.value.startswith(("brand","bot_name","website","color","texts.")) for o in view.children[0].options))
    async def test_setting_pagination(self):
        view=Settings(self.bot,9001,self.cfg,1)
        self.assertEqual(len(view.children[0].options),25);self.assertFalse(view.children[2].disabled)
    async def test_large_settings_are_not_silently_truncated(self):
        cfg=copy.deepcopy(self.cfg);cfg["blocked_users"]=[10**17+n for n in range(1000)]
        with self.assertRaisesRegex(ValueError,"Nothing was truncated"):SettingEditor(self.bot,9001,cfg,1,"blocked_users")
    async def test_custom_template_prefills_new_modal(self):
        spec=copy.deepcopy(self.cfg["templates"]["general"]);spec["title"]="Garage giveaway"
        modal=DraftDetails(self.bot,9001,"general",initial=spec)
        self.assertEqual(modal.field_title.default,"Garage giveaway")
    async def test_real_sdk_modal_component_limits(self):
        rid=await self.draft();r=await self.bot.db.get(1,rid)
        for modal in (DraftDetails(self.bot,9001,"general"),DraftJSON(self.bot,9001,r,"rules"),SettingEditor(self.bot,9001,self.cfg,1,"texts.join")):
            data=modal.to_dict();json.dumps(data);self.assertLessEqual(len(data["components"]),5);self.assertLessEqual(len(data["title"]),45)
    async def test_details_modal_submit_creates_real_private_draft(self):
        modal=DraftDetails(self.bot,9001,"general")
        for key,value in {"title":"Garage raffle","prize":"Test Hot Wheels","description":"A test campaign","duration":"5m","winners":"2"}.items():
            getattr(modal,"field_"+key)._value=value
        i=interaction(self.bot,self.guild);await modal.on_submit(i)
        rows=await self.bot.db.query("SELECT id FROM raffles WHERE guild=1")
        r=await self.bot.db.get(1,rows[0]["id"])
        self.assertEqual(r["status"],"draft");self.assertEqual(r["spec"]["duration"],300);self.assertEqual(r["spec"]["winners"],2)
        i.response.defer.assert_awaited_once()
    async def test_settings_modal_submit_changes_copy_without_rule_changes(self):
        r=await self.published();modal=SettingEditor(self.bot,9001,self.cfg,1,"texts.join");modal.field._value="Enter this raffle"
        await modal.on_submit(interaction(self.bot,self.guild))
        self.assertEqual((await self.bot.db.settings(1))[0]["texts"]["join"],"Enter this raffle")
        self.assertEqual((await self.bot.db.get(1,r["id"]))["contract_hash"],r["contract_hash"])
    async def test_public_buttons_are_persistent_and_namespaced(self):
        r=await self.published();view=public_view(r,self.cfg)
        self.assertTrue(view.is_persistent());self.assertEqual(len(view.children),5)
        self.assertTrue(all(x.item.custom_id.startswith("rcr:raffle:") for x in view.children))
    async def test_dynamic_button_reconstructs_after_restart(self):
        item=discord.ui.Button(label="Join raffle",custom_id="rcr:raffle:7:join")
        match=re.fullmatch(RaffleButton.__discord_ui_compiled_template__,item.custom_id)
        button=await RaffleButton.from_custom_id(None,item,match)
        self.assertEqual((button.rid,button.action),(7,"join"))
    async def test_confirmation_cannot_be_replayed(self):
        action=AsyncMock();view=Confirm(self.bot,9001,action)
        await view.children[0].callback(interaction(self.bot,self.guild))
        with self.assertRaises(ValueError):await view.children[0].callback(interaction(self.bot,self.guild))
        action.assert_awaited_once()
    async def test_confirmation_rechecks_fresh_manager_settings(self):
        manager=self.guild.add_member(1000,[10,30]);i=interaction(self.bot,self.guild,manager);action=AsyncMock();view=Confirm(self.bot,1000,action)
        cfg,rev=await self.bot.db.settings(1);cfg["manager_roles"]=[];await self.bot.db.save(1,cfg,rev,9001)
        with self.assertRaises(ValueError):await view.children[0].callback(i)
        action.assert_not_awaited();i.response.defer.assert_awaited_once()
    async def test_panel_owner_check(self):
        view=Hub(self.bot,9001);i=interaction(self.bot,self.guild,self.guild.add_member(1000,[10]))
        self.assertFalse(await view.interaction_check(i))
    async def test_button_acknowledges_before_slow_database(self):
        r=await self.published();user=self.guild.add_member(1000,[10]);i=interaction(self.bot,self.guild,user,self.guild.public.messages[r["message"]])
        async def slow(*args):
            self.assertTrue(i.response.is_done());return "You're in"
        self.bot.raffles.member_action=slow
        await RaffleButton(r["id"],"join","Join").callback(i)
        i.response.defer.assert_awaited_once();i.followup.send.assert_awaited_once()
    async def test_button_failure_public_reply_has_no_sensitive_exception(self):
        r=await self.published();user=self.guild.add_member(1000,[10]);i=interaction(self.bot,self.guild,user,self.guild.public.messages[r["message"]])
        self.bot.raffles.member_action=AsyncMock(side_effect=RuntimeError("Private exception detail"))
        with self.assertLogs("ripcars.raffle",level="ERROR"):await RaffleButton(r["id"],"join","Join").callback(i)
        self.assertNotIn("Private exception detail",str(i.followup.send.call_args));self.assertIn("RCR-",str(i.followup.send.call_args))
    async def test_cancelled_buttons_disabled(self):
        r=await self.published();r["status"]="cancelled";view=public_view(r,self.cfg)
        self.assertTrue(view.children[0].item.disabled);self.assertTrue(view.children[4].item.disabled)
    async def test_embed_limits_at_maximum_input(self):
        r=await self.draw_members(25);r["spec"].update(title="T"*100,prize="P"*400,description="D"*1200)
        r["spec"]["rules"].update(any_roles=list(range(10**17,10**17+20)),all_roles=list(range(10**18,10**18+20)),weights={str(n):100 for n in range(10**17,10**17+20)})
        cfg=copy.deepcopy(self.cfg);cfg["texts"]["entry_notice"]="N"*500
        embed=panel_embed(r,cfg,100000)
        self.assertLessEqual(len(embed),6000);self.assertTrue(all(len(x.value)<=1024 for x in embed.fields));self.assertIn("Full role lists",embed.fields[1].value)
    async def test_concurrent_join_leaves_panel_dirty_for_next_refresh(self):
        r=await self.published();msg=self.guild.public.messages[r["message"]];original=msg.edit
        async def during(**kwargs):
            await self.bot.db.enter(1,r["id"],1000);return await original(**kwargs)
        msg.edit=during;await self.bot.raffles.update_panel(self.guild,r["id"],True)
        self.assertEqual((await self.bot.db.get(1,r["id"]))["dirty"],1)
