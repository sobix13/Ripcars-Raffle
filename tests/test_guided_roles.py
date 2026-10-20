import copy
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import AsyncMock

import discord

from ripcars_raffle import config,eligibility
from ripcars_raffle.admin_ui import Manage,Setup
from ripcars_raffle.guided_ui import Home,SettingsHome,TicketDefaults,NewRaffle,RaffleActions,RoleControls,show_archive,show_participants
from ripcars_raffle.public_ui import panel_embed
from tests.fakes import AsyncCase,interaction,failure


class GuidedRoleTests(AsyncCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.guild._roles[50]._permissions|=discord.Permissions(manage_roles=True,view_audit_log=True).value
        self.entry=discord.Role(guild=self.guild,state=self.guild._state,data={'id':'40','name':'Raffle','permissions':'0','position':4,'color':0,'hoist':False,'managed':False,'mentionable':False})
        self.guild._roles[40]=self.entry
        self.cfg.update(raffle_role=40,role_tickets={'10':1,'11':3})
        await self.bot.db.save(1,self.cfg,1,9001)
        async def add(member,*roles,**kwargs):
            for role in roles:
                if role.id not in member._roles:member._roles.add(role.id)
        async def remove(member,*roles,**kwargs):
            for role in roles:
                if role.id in member._roles:member._roles.remove(role.id)
        for name,method in [('add_roles',add),('remove_roles',remove)]:
            p=patch.object(discord.Member,name,method);p.start();self.addCleanup(p.stop)
        self.events=[]
        async def audit_logs(**kwargs):
            for event in self.events:
                if event.id>kwargs['after'].id:yield event
        self.guild.audit_logs=audit_logs
    async def campaign(self,mode='auto'):
        spec=copy.deepcopy(self.cfg['templates']['general'])
        spec.update(prize='A Hot Wheels car',rules={**config.rules(),'any_roles':[10,11],'weights':{'10':1,'11':3},'weight_mode':'tickets','weight_cap':2000})
        rid=await self.bot.db.create(1,9001,'general',spec,result_mode=mode)
        return await self.bot.raffles.publish(self.guild,rid,9001)
    async def join(self,r,uid=1000,roles=None):
        member=self.guild.add_member(uid,roles or [10])
        await self.bot.raffles.member_action(interaction(self.bot,self.guild,member,self.guild.public.messages[r['message']]),r['id'],'join')
        return member
    def event(self,member,actor=9001,stamp=None):
        stamp=stamp or datetime.now(timezone.utc)
        return SimpleNamespace(id=discord.utils.time_snowflake(stamp,high=True),user_id=actor,target=member,before=SimpleNamespace(roles=[]),after=SimpleNamespace(roles=[self.entry]),created_at=stamp)
    async def test_ticket_sum_and_legacy_arithmetic_are_distinct(self):
        m=self.guild.add_member(1000,[10,11,12])
        rule={**config.rules(),'weights':{'10':1,'11':3},'weight_mode':'tickets','weight_cap':2000}
        self.assertEqual(eligibility.check(m,rule,10)[2],4)
        rule['weight_mode']='sum';self.assertEqual(eligibility.check(m,rule,10)[2],3)
    async def test_join_grants_role_once_and_keeps_one_entry(self):
        r=await self.campaign();m=await self.join(r)
        self.assertIn(40,m._roles)
        await self.bot.raffles.member_action(interaction(self.bot,self.guild,m,self.guild.public.messages[r['message']]),r['id'],'join')
        self.assertEqual(len(await self.bot.db.query('SELECT * FROM entries')),1)
    async def test_failed_role_write_preserves_entry_and_retries(self):
        r=await self.campaign()
        with patch.object(discord.Member,'add_roles',side_effect=failure()):m=await self.join(r)
        self.assertEqual(len(await self.bot.db.query('SELECT * FROM entries')),1)
        self.assertEqual((await self.bot.db.query('SELECT pending FROM role_changes'))[0]['pending'],1)
        await self.bot.raffles.sync_entry_roles(self.guild)
        self.assertIn(40,m._roles)
    async def test_manual_grant_enters_without_click_and_is_audited(self):
        r=await self.campaign();m=self.guild.add_member(1000,[10,40])
        self.events=[self.event(m)]
        await self.bot.raffles.sync_manual_roles(self.guild)
        await self.bot.raffles.sync_manual_roles(self.guild)
        rows=await self.bot.db.query('SELECT * FROM entry_approvals')
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['actor'],9001);self.assertEqual(rows[0]['method'],'manual')
        self.assertEqual((await self.bot.db.get(1,r['id']))['status'],'open')
    async def test_non_manager_grant_is_not_manual_approval(self):
        await self.campaign();m=self.guild.add_member(1000,[10,40]);self.events=[self.event(m,actor=1000)]
        await self.bot.raffles.sync_manual_roles(self.guild)
        self.assertEqual(await self.bot.db.query('SELECT * FROM entries'),[])
    async def test_review_mode_keeps_results_private_and_role_until_published(self):
        r=await self.campaign('review');m=await self.join(r)
        await self.bot.raffles.draw(self.guild,r['id'],9001,'Test close')
        await self.bot.raffles.update_panel(self.guild,r['id'],True)
        await self.bot.raffles.sync_entry_roles(self.guild)
        self.assertIn(40,m._roles);self.assertEqual(len(self.guild.public.sent),1)
        with self.assertRaises(ValueError):await self.bot.db.public_bundle(1,r['id'])
        await self.bot.db.execute('UPDATE raffles SET result_approved=1 WHERE id=?',(r['id'],))
        await self.bot.raffles.update_panel(self.guild,r['id'],True)
        await self.bot.raffles.sync_entry_roles(self.guild)
        self.assertNotIn(40,m._roles);self.assertEqual(len(self.guild.public.sent),2)
        self.assertEqual(len(await self.bot.db.query('SELECT * FROM entries')),1)
        self.assertTrue(await self.bot.db.public_bundle(1,r['id']))
    async def test_auto_results_announce_once_then_clear_role(self):
        r=await self.campaign();m=await self.join(r)
        await self.bot.raffles.draw(self.guild,r['id'],9001,'Test close')
        for _ in range(2):await self.bot.raffles.update_panel(self.guild,r['id'],True)
        await self.bot.raffles.sync_entry_roles(self.guild)
        self.assertEqual(len(self.guild.public.sent),2);self.assertNotIn(40,m._roles)
    async def test_ambiguous_announcement_is_recovered_without_resending(self):
        r=await self.campaign();m=await self.join(r)
        await self.bot.raffles.draw(self.guild,r['id'],9001,'Test close')
        original=self.guild.public.send
        async def uncertain(**kwargs):
            await original(**kwargs);raise OSError('Lost response')
        self.guild.public.send=uncertain
        with self.assertRaises(OSError):await self.bot.raffles.update_panel(self.guild,r['id'],True)
        await self.bot.raffles.update_panel(self.guild,r['id'],True)
        self.assertEqual((await self.bot.db.get(1,r['id']))['result_state'],'review')
        self.assertIn(40,m._roles);self.assertEqual(len(self.guild.public.sent),2)
        mid=max(self.guild.public.messages)
        await self.bot.raffles.recover_announcement(self.guild,r['id'],mid,9001)
        self.guild.public.send=original
        await self.bot.raffles.update_panel(self.guild,r['id'],True)
        await self.bot.raffles.sync_entry_roles(self.guild)
        self.assertEqual(len(self.guild.public.sent),2);self.assertNotIn(40,m._roles)
    async def test_manual_cleanup_keeps_registrations(self):
        r=await self.campaign();m=await self.join(r)
        await self.bot.raffles.clear_entry_role(self.guild,r['id'],9001)
        self.assertNotIn(40,m._roles)
        self.assertTrue((await self.bot.db.get(1,r['id']))['paused'])
        self.assertEqual(len(await self.bot.db.query('SELECT * FROM entries')),1)
    async def test_old_raffle_cannot_clear_new_raffle_role(self):
        old=await self.campaign();await self.join(old)
        await self.bot.raffles.draw(self.guild,old['id'],9001,'Close')
        await self.bot.raffles.update_panel(self.guild,old['id'],True)
        await self.bot.raffles.sync_entry_roles(self.guild)
        current=await self.campaign();member=await self.join(current,1001)
        with self.assertRaises(ValueError):await self.bot.raffles.clear_entry_role(self.guild,old['id'],9001)
        self.assertIn(40,member._roles)
    async def test_pending_results_block_next_raffle_even_if_no_roles_remain(self):
        r=await self.campaign('review');await self.join(r)
        await self.bot.raffles.draw(self.guild,r['id'],9001,'Close')
        await self.bot.raffles.clear_entry_role(self.guild,r['id'],9001)
        with self.assertRaises(ValueError):await self.campaign()
    async def test_public_count_changes_on_join_and_survives_role_cleanup(self):
        r=await self.campaign();await self.join(r)
        message=self.guild.public.messages[r['message']]
        self.assertIn('1 entered',str(message.embeds[0].to_dict()))
        await self.bot.raffles.clear_entry_role(self.guild,r['id'],9001)
        await self.bot.raffles.update_panel(self.guild,r['id'],True)
        self.assertIn('1 entered',str(message.embeds[0].to_dict()))
    async def test_uncertain_send_can_be_retried_only_after_reading_channel(self):
        r=await self.campaign();await self.join(r)
        await self.bot.raffles.draw(self.guild,r['id'],9001,'Close')
        await self.bot.db.execute("UPDATE raffles SET result_state='review' WHERE id=?",(r['id'],))
        async def history(**kwargs):
            for message in self.guild.public.messages.values():yield message
        self.guild.public.history=history
        await self.bot.raffles.review_announcement(self.guild,r['id'],9001)
        self.assertEqual(len(self.guild.public.sent),2)
        with self.assertRaises(ValueError):await self.bot.raffles.review_announcement(self.guild,r['id'],9001)
    async def test_history_pages_keep_older_raffles_accessible(self):
        for _ in range(27):
            rid=await self.draft();await self.bot.db.state_action(1,rid,9001,'cancel','Archive test')
        i=interaction(self.bot,self.guild)
        await show_archive(self.bot,i,'past')
        view=i.response.send_message.call_args.kwargs['view']
        self.assertEqual(len(view.children[0].options),25)
        j=interaction(self.bot,self.guild)
        await next(b for b in view.children if getattr(b,'label',None)=='Older raffles').callback(j)
        page=j.followup.send.call_args.kwargs['view']
        self.assertEqual(len(page.children[0].options),2)
        self.assertIn('Newer raffles',[getattr(b,'label',None) for b in page.children])
    async def test_participant_controls_fit_sdk_and_export(self):
        r=await self.campaign();await self.join(r)
        i=interaction(self.bot,self.guild);await show_participants(self.bot,i,r['id'])
        view=i.response.send_message.call_args.kwargs['view']
        self.assertLessEqual(len(view.to_components()),5)
        j=interaction(self.bot,self.guild)
        await next(b for b in view.children if getattr(b,'label',None)=='Export participants').callback(j)
        self.assertTrue(j.followup.send.call_args.kwargs['file'].filename.endswith('.csv'))
    async def test_unrelated_not_found_does_not_silently_exclude_member(self):
        r=await self.campaign();await self.join(r)
        self.guild.failures[1000]=failure(discord.NotFound,10011,404)
        with self.assertRaises(discord.NotFound):await self.bot.raffles.draw(self.guild,r['id'],9001,'Close')
        self.assertNotEqual((await self.bot.db.get(1,r['id']))['status'],'drawn')
    async def test_changed_draft_cannot_pass_old_publish_confirmation(self):
        rid=await self.draft();r=await self.bot.db.get(1,rid)
        view=Manage(self.bot,9001,r);i=interaction(self.bot,self.guild)
        await next(b for b in view.children if b.label=='Publish').callback(i)
        confirm=i.followup.send.call_args.kwargs['view']
        spec=copy.deepcopy(r['spec']);spec['prize']='Changed prize'
        await self.bot.db.edit(1,rid,spec,r['revision'],9001)
        with self.assertRaises(ValueError):await confirm.children[0].callback(interaction(self.bot,self.guild))
        self.assertEqual(self.guild.public.sent,[])
    async def test_quick_setup_creates_one_fixed_role_and_can_be_saved_again(self):
        cfg=copy.deepcopy(self.cfg);cfg['raffle_role']=0;cfg['role_tickets']={}
        await self.bot.db.save(1,cfg,2,9001)
        self.guild._roles.pop(40)
        async def create(**kwargs):
            self.guild._roles[40]=self.entry
            return self.entry
        self.guild.create_role=AsyncMock(side_effect=create)
        await self.bot.raffles.setup(self.guild,cfg,3,9001)
        saved,rev=await self.bot.db.settings(1)
        await self.bot.raffles.setup(self.guild,saved,rev,9001)
        self.guild.create_role.assert_awaited_once()
        self.assertEqual((await self.bot.db.settings(1))[0]['raffle_role'],40)
    async def test_all_guided_views_fit_real_discord_components(self):
        rid=await self.draft();r=await self.bot.db.get(1,rid)
        for view in (Home(self.bot,9001),SettingsHome(self.bot,9001,self.cfg,2),TicketDefaults(self.bot,9001,self.cfg,2),NewRaffle(self.bot,9001,self.cfg),RaffleActions(self.bot,9001,r),RoleControls(self.bot,9001,r),Setup(self.bot,9001,self.cfg,2)):
            self.assertLessEqual(len(view.to_components()),5)
        self.assertEqual(len(Home(self.bot,9001).children),4)
        r=await self.campaign();embed=panel_embed(r,self.cfg,3)
        self.assertLessEqual(len(embed.fields),4)
