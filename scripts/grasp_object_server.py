#!/usr/bin/python
# Software License Agreement (BSD License)
#
# Copyright (c) 2014, PAL Robotics SL
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of the PAL Robotics nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# author: Bence Magyar

#ros imports
import rospy
import tf
from actionlib import ActionServer, SimpleActionClient
from geometry_msgs.msg import PoseArray, Pose
from std_msgs.msg import Header
#own imports
from reem_tabletop_grasping.msg import GraspObjectAction 
from reem_tabletop_grasping.msg import GraspObjectFeedback
#perception imports & grasp planning imports
from object_recognition_msgs.msg import RecognizedObjectArray
from object_recognition_clusters import ClusterBoundingBoxFinder
from block_grasp_generator.msg import GenerateBlockGraspsAction, GenerateBlockGraspsGoal
#manipulation imports
from moveit_msgs.msg import Grasp, PickupAction, PickupGoal, PickupResult, GripperTranslation, MoveItErrorCodes
from moveit_commander import RobotCommander, PlanningSceneInterface


class GraspObjectServer:

    def __init__(self, name):
        # stuff for grasp planning
        self.tf_listener = tf.TransformListener()
        self.tf_broadcaster = tf.TransformBroadcaster()
        self.cbbf = ClusterBoundingBoxFinder(self.tf_listener, self.tf_broadcaster, "base_link")
        self.last_objects = RecognizedObjectArray()
        #rospy.Subscriber("object_array", RecognizedObjectArray, self.objects_callback)
        self.sub = rospy.Subscriber("/recognized_object_array", RecognizedObjectArray, self.objects_callback)
        self.grasp_publisher = rospy.Publisher("grasp_pose_from_block_bla", PoseArray)
        
        rospy.loginfo("Connecting to pickup AS")
        self.pickup_ac = SimpleActionClient('/pickup', PickupAction)
        #pickup_ac.wait_for_server() # needed?
        
        rospy.loginfo("Connecting to grasp generator AS")
        self.grasps_ac = SimpleActionClient('/grasp_generator_server/generate', GenerateBlockGraspsAction)
        #grasps_ac.wait_for_server() # needed? 
        
        
        # blocking action server
        self.grasp_obj_as = ActionServer(name, GraspObjectAction, self.goal_callback, self.cancel_callback, False)
        self.feedback = GraspObjectFeedback()
        self.current_goal = None
        self.grasp_obj_as.start()

    def objects_callback(self, data):
        rospy.loginfo(rospy.get_name() + ": This message contains %d objects." % len(data.objects))
        #rospy.loginfo(data.objects[0].point_clouds[0].header.frame_id)
        self.last_objects = data
        
    def goal_callback(self, goal):      
        if self.current_goal:
          goal.set_rejected("Server busy")
          return
        elif len(self.last_objects.objects) - 1 < goal.get_goal().target_id:
          goal.set_rejected("No objects to grasp were received on the objects topic.")
          return
        else:
          #store and accept new goal
          self.current_goal = goal
          self.current_goal.set_accepted()
          #run grasping state machine
          self.grasping_sm()
          #finished, get rid of goal
          self.current_goal = None
        
    def cancel_callback(self, goal):
        #TODO stop motions?
        self.current_goal.set_canceled()

    def grasping_sm(self):
      if self.current_goal:
        self.update_feedback("Running clustering")
        (object_points, object_bounding_box_dims, 
         object_bounding_box, object_pose) = self.cbbf.find_object_frame_and_bounding_box(self.last_objects.objects[self.current_goal.get_goal().target_id].point_clouds[0])
        #TODO visualize bbox
        print object_bounding_box_dims
        self.update_feedback("check reachability")
        rospy.sleep(1.0)
        self.update_feedback("generate grasps")
        # pose + width is the bbox size on x
        grasp_list = self.generate_grasps(object_pose, object_bounding_box_dims[0])
        self.publish_grasps_as_poses(grasp_list)
        self.update_feedback("setup planning scene")
        rospy.sleep(1.0)
        self.update_feedback("execute grasps")
        rospy.sleep(1.0)
        self.current_goal.set_succeeded()
        #self.current_goal.set_aborted()
        
    def update_feedback(self, text):
        self.feedback.last_state = text
        self.current_goal.publish_feedback(self.feedback)
        
    def generate_grasps(self, pose, width):
          self.grasps_ac.wait_for_server()
          rospy.loginfo("Succesfully connected.")
          goal = GenerateBlockGraspsGoal()
          goal.pose = pose.pose
          goal.width = width
          self.grasps_ac.send_goal(goal)
          rospy.loginfo("Sent goal, waiting:\n" + str(goal))
          t_start = rospy.Time.now()
          self.grasps_ac.wait_for_result()
          t_end = rospy.Time.now()
          t_total = t_end - t_start
          rospy.loginfo("Result received in " + str(t_total.to_sec()))
          grasp_list = self.grasps_ac.get_result().grasps
          return grasp_list

    def publish_grasps_as_poses(self, grasps):
          rospy.loginfo("Publishing PoseArray on /grasp_pose_from_block_bla for grasp_pose")
          graspmsg = Grasp()
          grasp_PA = PoseArray()
          header = Header()
          header.frame_id = "base_link"
          header.stamp = rospy.Time.now()
          grasp_PA.header = header
          for graspmsg in grasps:
              #print graspmsg
              #print type(graspmsg)
              p = Pose(graspmsg.grasp_pose.pose.position, graspmsg.grasp_pose.pose.orientation)
              grasp_PA.poses.append(p)
          self.grasp_publisher.publish(grasp_PA)
          rospy.sleep(0.1)


if __name__ == '__main__':
    name = 'grasp_object_server'
    rospy.init_node(name, anonymous=False)
    server = GraspObjectServer(name)
    rospy.loginfo(name + ": Ready to roll.")
    rospy.spin()
